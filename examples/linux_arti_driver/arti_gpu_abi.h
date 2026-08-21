/* SPDX-License-Identifier: MIT */
#ifndef ARTI_GPU_ABI_H
#define ARTI_GPU_ABI_H

/*
 * Minimal ARTI GPU control ABI.
 *
 * The boot display path uses Linux simple-framebuffer at reset. A future RTL
 * display controller should expose these registers at the ctrl resource so a
 * Linux GPU driver can take over the scanout configuration after boot.
 */

#define ARTI_GPU_REG_ID             0x000
#define ARTI_GPU_REG_VERSION        0x004
#define ARTI_GPU_REG_FB_BASE_LO     0x010
#define ARTI_GPU_REG_FB_BASE_HI     0x014
#define ARTI_GPU_REG_WIDTH          0x018
#define ARTI_GPU_REG_HEIGHT         0x01c
#define ARTI_GPU_REG_STRIDE         0x020
#define ARTI_GPU_REG_FORMAT         0x024
#define ARTI_GPU_REG_CONTROL        0x028
#define ARTI_GPU_REG_IRQ_STATUS     0x030
#define ARTI_GPU_REG_IRQ_MASK       0x034

#define ARTI_GPU_ID                 0x41525449u
#define ARTI_GPU_VERSION_1          0x00010000u

#define ARTI_GPU_FORMAT_A8R8G8B8    1u

#define ARTI_GPU_CONTROL_ENABLE     0x00000001u
#define ARTI_GPU_CONTROL_VSYNC_IRQ  0x00000002u

#define ARTI_GPU_IRQ_VSYNC          0x00000001u

#endif
