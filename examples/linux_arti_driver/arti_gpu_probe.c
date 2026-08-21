// SPDX-License-Identifier: GPL-2.0
#include <linux/io.h>
#include <linux/interrupt.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/sched.h>
#include <linux/slab.h>
#include <linux/wait.h>

#include "arti_gpu_abi.h"

struct arti_gpu_probe {
    void __iomem *ctrl;
    void __iomem *fb;
    resource_size_t ctrl_phys;
    resource_size_t ctrl_size;
    resource_size_t fb_phys;
    resource_size_t fb_size;
    u32 width;
    u32 height;
    u32 stride;
    const char *format;
    wait_queue_head_t irq_wait;
    atomic_t irq_count;
};

static bool program_mode;
module_param(program_mode, bool, 0644);
MODULE_PARM_DESC(program_mode, "program the minimal ARTI GPU scanout registers");

static bool fill_pattern;
module_param(fill_pattern, bool, 0644);
MODULE_PARM_DESC(fill_pattern, "write a small a8r8g8b8 color pattern into the boot framebuffer");

static struct resource *arti_gpu_resource(struct platform_device *pdev,
                                          const char *name,
                                          unsigned int index)
{
    struct resource *res;

    res = platform_get_resource_byname(pdev, IORESOURCE_MEM, name);
    if (res)
        return res;

    return platform_get_resource(pdev, IORESOURCE_MEM, index);
}

static int arti_gpu_read_boot_mode(struct platform_device *pdev,
                                   struct arti_gpu_probe *gpu)
{
    struct device_node *np = pdev->dev.of_node;
    const __be32 *fb_prop;
    int len;

    if (!np)
        return -ENODEV;

    of_property_read_u32(np, "arti,boot-width", &gpu->width);
    of_property_read_u32(np, "arti,boot-height", &gpu->height);
    of_property_read_u32(np, "arti,boot-stride", &gpu->stride);
    of_property_read_string(np, "arti,boot-format", &gpu->format);

    fb_prop = of_get_property(np, "arti,boot-framebuffer", &len);
    if (fb_prop && len >= 4 * sizeof(__be32)) {
        gpu->fb_phys = ((u64)be32_to_cpup(fb_prop) << 32) |
                       be32_to_cpup(fb_prop + 1);
        gpu->fb_size = ((u64)be32_to_cpup(fb_prop + 2) << 32) |
                       be32_to_cpup(fb_prop + 3);
    }

    if (!gpu->format)
        gpu->format = "unknown";

    if (!gpu->width || !gpu->height ||
        (u64)gpu->stride < (u64)gpu->width * 4u ||
        strcmp(gpu->format, "a8r8g8b8"))
        return -EINVAL;

    return 0;
}

static void arti_gpu_program_mode(struct arti_gpu_probe *gpu)
{
    iowrite32(lower_32_bits(gpu->fb_phys), gpu->ctrl + ARTI_GPU_REG_FB_BASE_LO);
    iowrite32(upper_32_bits(gpu->fb_phys), gpu->ctrl + ARTI_GPU_REG_FB_BASE_HI);
    iowrite32(gpu->width, gpu->ctrl + ARTI_GPU_REG_WIDTH);
    iowrite32(gpu->height, gpu->ctrl + ARTI_GPU_REG_HEIGHT);
    iowrite32(gpu->stride, gpu->ctrl + ARTI_GPU_REG_STRIDE);
    iowrite32(ARTI_GPU_FORMAT_A8R8G8B8, gpu->ctrl + ARTI_GPU_REG_FORMAT);
    iowrite32(ARTI_GPU_CONTROL_ENABLE, gpu->ctrl + ARTI_GPU_REG_CONTROL);
}

static void arti_gpu_fill_pattern(struct arti_gpu_probe *gpu)
{
    u32 x;
    u32 y;
    u32 max_width;
    u32 max_height;

    if (!gpu->fb || !gpu->width || !gpu->height || !gpu->stride)
        return;

    max_width = min_t(u32, gpu->width, gpu->stride / sizeof(u32));
    max_height = min_t(u32, gpu->height, gpu->fb_size / gpu->stride);

    for (y = 0; y < max_height; y++) {
        void __iomem *row = gpu->fb + y * gpu->stride;

        for (x = 0; x < max_width; x++) {
            u8 r = (x * 255u) / max_t(u32, max_width - 1, 1);
            u8 g = (y * 255u) / max_t(u32, max_height - 1, 1);
            u8 b = ((x ^ y) & 0xff);
            u32 pixel = 0xff000000u | (r << 16) | (g << 8) | b;

            iowrite32(pixel, row + x * sizeof(pixel));
        }
        cond_resched();
    }
}

static irqreturn_t arti_gpu_irq(int irq, void *data)
{
    struct arti_gpu_probe *gpu = data;

    atomic_inc(&gpu->irq_count);
    iowrite32(ARTI_GPU_IRQ_VSYNC,
              gpu->ctrl + ARTI_GPU_REG_IRQ_STATUS);
    wake_up(&gpu->irq_wait);
    return IRQ_HANDLED;
}

static int arti_gpu_probe(struct platform_device *pdev)
{
    struct arti_gpu_probe *gpu;
    struct resource *ctrl;
    struct resource *fb;
    int irq;
    int ret;
    u32 id;
    u32 version;
    long irq_waited;
    unsigned poll;

    gpu = devm_kzalloc(&pdev->dev, sizeof(*gpu), GFP_KERNEL);
    if (!gpu)
        return -ENOMEM;
    init_waitqueue_head(&gpu->irq_wait);
    atomic_set(&gpu->irq_count, 0);

    ret = arti_gpu_read_boot_mode(pdev, gpu);
    if (ret)
        return ret;

    ctrl = arti_gpu_resource(pdev, "ctrl", 0);
    if (!ctrl)
        return dev_err_probe(&pdev->dev, -ENODEV, "missing ctrl resource\n");

    gpu->ctrl_phys = ctrl->start;
    gpu->ctrl_size = resource_size(ctrl);
    gpu->ctrl = devm_ioremap_resource(&pdev->dev, ctrl);
    if (IS_ERR(gpu->ctrl))
        return PTR_ERR(gpu->ctrl);

    fb = arti_gpu_resource(pdev, "fb", 1);
    if (!fb)
        return dev_err_probe(&pdev->dev, -ENODEV, "missing fb resource\n");

    gpu->fb_phys = fb->start;
    gpu->fb_size = resource_size(fb);
    if ((u64)gpu->stride * gpu->height > gpu->fb_size)
        return dev_err_probe(&pdev->dev, -EINVAL,
                             "framebuffer resource is too small\n");
    /*
     * The boot simplefb driver may still own this memory resource. Map it
     * without requesting the resource; the full DRM driver will explicitly
     * remove the firmware/simplefb aperture before taking ownership.
     */
    gpu->fb = devm_ioremap(&pdev->dev, gpu->fb_phys, gpu->fb_size);
    if (!gpu->fb)
        return dev_err_probe(&pdev->dev, -ENOMEM, "cannot map fb resource\n");

    id = ioread32(gpu->ctrl + ARTI_GPU_REG_ID);
    version = ioread32(gpu->ctrl + ARTI_GPU_REG_VERSION);
    if (id != ARTI_GPU_ID || version != ARTI_GPU_VERSION_1)
        return dev_err_probe(&pdev->dev, -ENODEV,
                             "unsupported GPU ABI id=0x%08x version=0x%08x\n",
                             id, version);
    dev_info(&pdev->dev, "ARTI GPU ABI: ID=0x%08x VERSION=0x%08x\n",
             id, version);

    platform_set_drvdata(pdev, gpu);

    irq = platform_get_irq_optional(pdev, 0);
    if (irq < 0)
        return dev_err_probe(&pdev->dev, irq,
                             "missing VSYNC IRQ resource\n");
    ret = devm_request_irq(&pdev->dev, irq, arti_gpu_irq, 0,
                           dev_name(&pdev->dev), gpu);
    if (ret)
        return dev_err_probe(&pdev->dev, ret,
                             "cannot request VSYNC IRQ %d\n", irq);
    dev_info(&pdev->dev, "irq 0 mapped to Linux IRQ %d\n", irq);

    iowrite32(ARTI_GPU_IRQ_VSYNC, gpu->ctrl + ARTI_GPU_REG_IRQ_MASK);
    iowrite32(ARTI_GPU_CONTROL_ENABLE | ARTI_GPU_CONTROL_VSYNC_IRQ,
              gpu->ctrl + ARTI_GPU_REG_CONTROL);
    /* Each embedded-model MMIO transaction advances the RTL clock. */
    for (poll = 0; poll < 4096 && atomic_read(&gpu->irq_count) == 0; poll++)
        ioread32(gpu->ctrl + ARTI_GPU_REG_IRQ_STATUS);
    irq_waited = wait_event_timeout(gpu->irq_wait,
                                    atomic_read(&gpu->irq_count) != 0,
                                    msecs_to_jiffies(250));
    iowrite32(ARTI_GPU_IRQ_VSYNC, gpu->ctrl + ARTI_GPU_REG_IRQ_STATUS);
    iowrite32(ARTI_GPU_CONTROL_ENABLE, gpu->ctrl + ARTI_GPU_REG_CONTROL);
    iowrite32(0, gpu->ctrl + ARTI_GPU_REG_IRQ_MASK);
    if (!irq_waited)
        return dev_err_probe(&pdev->dev, -ETIMEDOUT,
                             "VSYNC IRQ did not arrive\n");
    dev_info(&pdev->dev, "ARTI GPU IRQ PASS: VSYNC delivered (%d)\n",
             atomic_read(&gpu->irq_count));

    dev_info(&pdev->dev,
             "ARTI GPU probe: ctrl=%pa+%pa fb=%pa+%pa mode=%ux%u stride=%u format=%s\n",
             &gpu->ctrl_phys, &gpu->ctrl_size, &gpu->fb_phys, &gpu->fb_size,
             gpu->width, gpu->height, gpu->stride, gpu->format);
    dev_info(&pdev->dev, "ARTI GPU PROBE PASS: resources mapped\n");

    if (program_mode) {
        arti_gpu_program_mode(gpu);
        dev_info(&pdev->dev, "programmed minimal scanout registers\n");
    }

    if (fill_pattern) {
        arti_gpu_fill_pattern(gpu);
        dev_info(&pdev->dev, "wrote framebuffer test pattern\n");
    }

    return 0;
}

static const struct of_device_id arti_gpu_of_match[] = {
    { .compatible = "arti,rtl-gpu" },
    { }
};
MODULE_DEVICE_TABLE(of, arti_gpu_of_match);

static struct platform_driver arti_gpu_driver = {
    .probe = arti_gpu_probe,
    .driver = {
        .name = "arti-gpu-probe",
        .of_match_table = arti_gpu_of_match,
    },
};
module_platform_driver(arti_gpu_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ARTI minimal GPU RTL probe driver");
