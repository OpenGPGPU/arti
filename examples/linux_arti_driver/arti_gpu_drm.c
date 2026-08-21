// SPDX-License-Identifier: GPL-2.0
#include <linux/aperture.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>

#include <drm/clients/drm_client_setup.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_connector.h>
#include <drm/drm_drv.h>
#include <drm/drm_edid.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_gem_framebuffer_helper.h>
#include <drm/drm_fbdev_shmem.h>
#include <drm/drm_gem_shmem_helper.h>
#include <drm/drm_mode_config.h>
#include <drm/drm_probe_helper.h>
#include <drm/drm_simple_kms_helper.h>

#include "arti_gpu_abi.h"

#define ARTI_GPU_DEFAULT_WIDTH  1024u
#define ARTI_GPU_DEFAULT_HEIGHT 768u
#define ARTI_GPU_DEFAULT_STRIDE 4096u

struct arti_gpu_drm {
    struct drm_device drm;
    struct drm_simple_display_pipe pipe;
    struct drm_connector connector;
    void __iomem *ctrl;
    void __iomem *fb;
    resource_size_t ctrl_phys;
    resource_size_t fb_phys;
    resource_size_t fb_size;
    u32 width;
    u32 height;
    u32 stride;
};

static inline struct arti_gpu_drm *to_arti_gpu_drm(struct drm_device *drm)
{
    return container_of(drm, struct arti_gpu_drm, drm);
}

static inline struct arti_gpu_drm *pipe_to_arti_gpu(struct drm_simple_display_pipe *pipe)
{
    return container_of(pipe, struct arti_gpu_drm, pipe);
}

static int arti_gpu_connector_get_modes(struct drm_connector *connector)
{
    struct arti_gpu_drm *gpu = container_of(connector, struct arti_gpu_drm,
                                            connector);
    int count;

    count = drm_add_modes_noedid(connector, gpu->width, gpu->height);
    drm_set_preferred_mode(connector, gpu->width, gpu->height);
    return count;
}

static const struct drm_connector_helper_funcs arti_gpu_connector_helper_funcs = {
    .get_modes = arti_gpu_connector_get_modes,
};

static const struct drm_connector_funcs arti_gpu_connector_funcs = {
    .reset = drm_atomic_helper_connector_reset,
    .fill_modes = drm_helper_probe_single_connector_modes,
    .destroy = drm_connector_cleanup,
    .atomic_duplicate_state = drm_atomic_helper_connector_duplicate_state,
    .atomic_destroy_state = drm_atomic_helper_connector_destroy_state,
};

static enum drm_mode_status
arti_gpu_mode_valid(struct drm_simple_display_pipe *pipe,
                    const struct drm_display_mode *mode)
{
    struct arti_gpu_drm *gpu = pipe_to_arti_gpu(pipe);

    if (mode->hdisplay != gpu->width || mode->vdisplay != gpu->height)
        return MODE_BAD;

    return MODE_OK;
}

static void arti_gpu_set_scanout(struct arti_gpu_drm *gpu, bool enable)
{
    iowrite32(lower_32_bits(gpu->fb_phys),
              gpu->ctrl + ARTI_GPU_REG_FB_BASE_LO);
    iowrite32(upper_32_bits(gpu->fb_phys),
              gpu->ctrl + ARTI_GPU_REG_FB_BASE_HI);
    iowrite32(gpu->width, gpu->ctrl + ARTI_GPU_REG_WIDTH);
    iowrite32(gpu->height, gpu->ctrl + ARTI_GPU_REG_HEIGHT);
    iowrite32(gpu->stride, gpu->ctrl + ARTI_GPU_REG_STRIDE);
    iowrite32(ARTI_GPU_FORMAT_A8R8G8B8, gpu->ctrl + ARTI_GPU_REG_FORMAT);
    iowrite32(enable ? ARTI_GPU_CONTROL_ENABLE : 0,
              gpu->ctrl + ARTI_GPU_REG_CONTROL);
}

static void arti_gpu_copy_framebuffer(struct arti_gpu_drm *gpu,
                                      struct drm_framebuffer *fb)
{
    struct iosys_map map;
    size_t copy_pitch;
    void *line;
    u32 y;

    if (fb->format->format != DRM_FORMAT_ARGB8888 &&
        fb->format->format != DRM_FORMAT_XRGB8888)
        return;

    copy_pitch = min_t(size_t, fb->pitches[0], gpu->stride);
    copy_pitch = min_t(size_t, copy_pitch, gpu->fb_size / gpu->height);
    if (!copy_pitch)
        return;

    line = kmalloc(copy_pitch, GFP_KERNEL);
    if (!line)
        return;

    if (drm_gem_fb_vmap(fb, &map, NULL))
        goto out_free;

    for (y = 0; y < gpu->height; y++) {
        size_t src_offset = fb->offsets[0] + (size_t)y * fb->pitches[0];

        iosys_map_memcpy_from(line, &map, src_offset, copy_pitch);
        memcpy_toio(gpu->fb + (size_t)y * gpu->stride, line, copy_pitch);
    }
    drm_gem_fb_vunmap(fb, &map);

out_free:
    kfree(line);
}

static void arti_gpu_pipe_enable(struct drm_simple_display_pipe *pipe,
                                  struct drm_crtc_state *crtc_state,
                                  struct drm_plane_state *plane_state)
{
    struct arti_gpu_drm *gpu = pipe_to_arti_gpu(pipe);

    arti_gpu_set_scanout(gpu, true);
    if (plane_state->fb)
        arti_gpu_copy_framebuffer(gpu, plane_state->fb);
}

static void arti_gpu_pipe_disable(struct drm_simple_display_pipe *pipe)
{
    arti_gpu_set_scanout(pipe_to_arti_gpu(pipe), false);
}

static void arti_gpu_pipe_update(struct drm_simple_display_pipe *pipe,
                                 struct drm_plane_state *old_plane_state)
{
    struct drm_plane_state *plane_state = pipe->plane.state;

    if (plane_state->fb)
        arti_gpu_copy_framebuffer(pipe_to_arti_gpu(pipe), plane_state->fb);
}

static const struct drm_simple_display_pipe_funcs arti_gpu_pipe_funcs = {
    .mode_valid = arti_gpu_mode_valid,
    .enable = arti_gpu_pipe_enable,
    .disable = arti_gpu_pipe_disable,
    .update = arti_gpu_pipe_update,
};

static const struct drm_mode_config_funcs arti_gpu_mode_config_funcs = {
    .fb_create = drm_gem_fb_create,
    .atomic_check = drm_atomic_helper_check,
    .atomic_commit = drm_atomic_helper_commit,
};

DEFINE_DRM_GEM_FOPS(arti_gpu_fops);

static const struct drm_driver arti_gpu_drm_driver = {
    .driver_features = DRIVER_MODESET | DRIVER_GEM | DRIVER_ATOMIC,
    .name = "arti_gpu",
    .desc = "ARTI RTL framebuffer display controller",
    .major = 1,
    .minor = 0,
    .fops = &arti_gpu_fops,
    DRM_GEM_SHMEM_DRIVER_OPS,
    DRM_FBDEV_SHMEM_DRIVER_OPS,
};

static int arti_gpu_drm_probe(struct platform_device *pdev)
{
    struct arti_gpu_drm *gpu;
    struct drm_device *drm;
    struct resource *ctrl;
    struct resource *fb;
    const char *format;
    u32 id;
    u32 version;
    int ret;

    gpu = devm_drm_dev_alloc(&pdev->dev, &arti_gpu_drm_driver,
                             struct arti_gpu_drm, drm);
    if (IS_ERR(gpu))
        return PTR_ERR(gpu);
    drm = &gpu->drm;

    gpu->width = ARTI_GPU_DEFAULT_WIDTH;
    gpu->height = ARTI_GPU_DEFAULT_HEIGHT;
    gpu->stride = ARTI_GPU_DEFAULT_STRIDE;
    of_property_read_u32(pdev->dev.of_node, "arti,boot-width", &gpu->width);
    of_property_read_u32(pdev->dev.of_node, "arti,boot-height", &gpu->height);
    of_property_read_u32(pdev->dev.of_node, "arti,boot-stride", &gpu->stride);
    if (of_property_read_string(pdev->dev.of_node, "arti,boot-format", &format) == 0 &&
        strcmp(format, "a8r8g8b8"))
        return dev_err_probe(&pdev->dev, -EINVAL,
                             "unsupported framebuffer format %s\n", format);

    if (!gpu->width || !gpu->height || gpu->stride < gpu->width * 4u)
        return dev_err_probe(&pdev->dev, -EINVAL, "invalid boot mode\n");

    ctrl = platform_get_resource_byname(pdev, IORESOURCE_MEM, "ctrl");
    fb = platform_get_resource_byname(pdev, IORESOURCE_MEM, "fb");
    if (!ctrl || !fb)
        return dev_err_probe(&pdev->dev, -ENODEV,
                             "missing ctrl/fb resources\n");

    gpu->ctrl_phys = ctrl->start;
    gpu->fb_phys = fb->start;
    gpu->fb_size = resource_size(fb);
    if ((u64)gpu->stride * gpu->height > gpu->fb_size)
        return dev_err_probe(&pdev->dev, -EINVAL,
                             "framebuffer resource is too small\n");

    gpu->ctrl = devm_ioremap_resource(&pdev->dev, ctrl);
    if (IS_ERR(gpu->ctrl))
        return PTR_ERR(gpu->ctrl);

    id = ioread32(gpu->ctrl + ARTI_GPU_REG_ID);
    version = ioread32(gpu->ctrl + ARTI_GPU_REG_VERSION);
    if (id != ARTI_GPU_ID || version != ARTI_GPU_VERSION_1)
        return dev_err_probe(&pdev->dev, -ENODEV,
                             "unsupported GPU ABI id=0x%08x version=0x%08x\n",
                             id, version);

    ret = aperture_remove_conflicting_devices(fb->start, resource_size(fb),
                                               arti_gpu_drm_driver.name);
    if (ret)
        return dev_err_probe(&pdev->dev, ret,
                             "cannot remove conflicting framebuffer\n");

    ret = devm_aperture_acquire_for_platform_device(pdev, fb->start,
                                                     resource_size(fb));
    if (ret)
        return dev_err_probe(&pdev->dev, ret,
                             "cannot take over simplefb aperture\n");

    gpu->fb = devm_ioremap(&pdev->dev, fb->start, resource_size(fb));
    if (!gpu->fb)
        return dev_err_probe(&pdev->dev, -ENOMEM,
                             "cannot map framebuffer resource\n");

    ret = drmm_mode_config_init(drm);
    if (ret)
        return ret;
    drm->mode_config.min_width = gpu->width;
    drm->mode_config.max_width = gpu->width;
    drm->mode_config.min_height = gpu->height;
    drm->mode_config.max_height = gpu->height;
    drm->mode_config.preferred_depth = 32;
    drm->mode_config.funcs = &arti_gpu_mode_config_funcs;

    drm_connector_helper_add(&gpu->connector,
                             &arti_gpu_connector_helper_funcs);
    ret = drm_connector_init(drm, &gpu->connector, &arti_gpu_connector_funcs,
                             DRM_MODE_CONNECTOR_VIRTUAL);
    if (ret)
        return ret;

    ret = drm_simple_display_pipe_init(drm, &gpu->pipe, &arti_gpu_pipe_funcs,
                                       (const u32[]){ DRM_FORMAT_ARGB8888,
                                                      DRM_FORMAT_XRGB8888 },
                                       2, NULL, &gpu->connector);
    if (ret)
        return ret;

    drm_mode_config_reset(drm);
    platform_set_drvdata(pdev, drm);
    ret = drm_dev_register(drm, 0);
    if (ret)
        return ret;

    dev_info(&pdev->dev,
             "ARTI GPU DRM ready: %ux%u stride=%u fb=%pa\n",
             gpu->width, gpu->height, gpu->stride, &gpu->fb_phys);
    dev_info(&pdev->dev, "ARTI GPU DRM PASS: registered and took over simplefb\n");
    drm_client_setup(drm, NULL);
    return 0;
}

static void arti_gpu_drm_remove(struct platform_device *pdev)
{
    struct drm_device *drm = platform_get_drvdata(pdev);

    drm_dev_unplug(drm);
    drm_atomic_helper_shutdown(drm);
}

static const struct of_device_id arti_gpu_drm_of_match[] = {
    { .compatible = "arti,rtl-gpu" },
    { }
};
MODULE_DEVICE_TABLE(of, arti_gpu_drm_of_match);

static struct platform_driver arti_gpu_drm_platform_driver = {
    .probe = arti_gpu_drm_probe,
    .remove = arti_gpu_drm_remove,
    .driver = {
        .name = "arti-gpu",
        .of_match_table = arti_gpu_drm_of_match,
    },
};
module_platform_driver(arti_gpu_drm_platform_driver);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("ARTI RTL minimal DRM/KMS driver");
