// SPDX-License-Identifier: GPL-2.0
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>

#include "arti_gpu_abi.h"

#define ARTI_BASE 0x0b000000ULL
#define ARTI_SIZE 0x1000
#define ARTI_TEST_VALUE 0x123456a5U

static struct platform_device *arti_pdev;

static int arti_probe(struct platform_device *pdev)
{
    struct resource *res;
    void __iomem *base;
    u32 value;
    u32 version;

    res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
    /* Keep this legacy loopback test non-exclusive so the GPU probe can
     * validate the same control aperture in the same initramfs. */
    base = devm_ioremap(&pdev->dev, res->start, resource_size(res));
    if (!base)
        return -ENOMEM;

    value = ioread32(base + ARTI_GPU_REG_ID);
    if (value == ARTI_GPU_ID) {
        version = ioread32(base + ARTI_GPU_REG_VERSION);
        if (version != ARTI_GPU_VERSION_1) {
            dev_err(&pdev->dev, "ARTI GPU FAIL: ID 0x%08x version 0x%08x\n",
                    value, version);
            return -ENODEV;
        }
        dev_info(&pdev->dev,
                 "ARTI GPU ABI PASS: ID 0x%08x version 0x%08x\n",
                 value, version);
        return 0;
    }

    iowrite32(ARTI_TEST_VALUE, base);
    value = ioread32(base);
    if (value != ARTI_TEST_VALUE) {
        dev_err(&pdev->dev, "ARTI FAIL: wrote 0x%08x read 0x%08x\n",
                ARTI_TEST_VALUE, value);
        return -EIO;
    }
    dev_info(&pdev->dev, "ARTI LINUX PASS: read back 0x%08x\n", value);
    return 0;
}

static struct platform_driver arti_driver = {
    .probe = arti_probe,
    .driver = { .name = "arti-rtl-test" },
};

static int __init arti_init(void)
{
    struct resource res = {
        .start = ARTI_BASE,
        .end = ARTI_BASE + ARTI_SIZE - 1,
        .flags = IORESOURCE_MEM,
    };
    int ret;

    ret = platform_driver_register(&arti_driver);
    if (ret)
        return ret;
    arti_pdev = platform_device_register_simple("arti-rtl-test", -1, &res, 1);
    if (IS_ERR(arti_pdev)) {
        ret = PTR_ERR(arti_pdev);
        platform_driver_unregister(&arti_driver);
        return ret;
    }
    return 0;
}

static void __exit arti_exit(void)
{
    platform_device_unregister(arti_pdev);
    platform_driver_unregister(&arti_driver);
}

module_init(arti_init);
module_exit(arti_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ARTI native QEMU SysBus MMIO loopback test");
