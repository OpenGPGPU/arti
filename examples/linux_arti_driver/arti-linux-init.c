/* SPDX-License-Identifier: GPL-2.0 */
/* Minimal static init for ARTI Linux device test.
 * Mounts core filesystems, loads arti_rtl_test.ko, prints result,
 * then powers off. The driver's probe prints ARTI LINUX PASS/FAIL
 * to the kernel console (ttyAMA0). */
#include <sys/syscall.h>
#include <sys/mount.h>
#include <sys/reboot.h>
#include <linux/reboot.h>
#include <fcntl.h>
#include <unistd.h>
#include <string.h>

#ifndef __NR_finit_module
#define __NR_finit_module 413
#endif

static int putstr(const char *s) {
    return write(1, s, strlen(s));
}

static char *u32hex(unsigned v, char *buf) {
    char hex[] = "0123456789abcdef";
    for (int i = 7; i >= 0; i--) { buf[i] = hex[v & 0xf]; v >>= 4; }
    buf[8] = '\r'; buf[9] = '\n'; buf[10] = 0;
    return buf;
}

static int load_module(const char *path, const char *options, const char *name) {
    int fd = open(path, O_RDONLY);
    int ret;

    if (fd < 0)
        return -1;
    ret = syscall(__NR_finit_module, fd, options, 0);
    close(fd);
    if (ret == 0) {
        putstr("ARTI Linux init: loaded ");
        putstr(name);
        putstr("\r\n");
    }
    return ret;
}

int main(void) {
    /* Open console for output */
    int console = open("/dev/console", O_WRONLY);
    if (console >= 0) { dup2(console, 1); dup2(console, 2); }

    /* Mount core filesystems */
    mount("proc", "/proc", "proc", 0, NULL);
    mount("sysfs", "/sys", "sysfs", 0, NULL);
    mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
    /* Re-open console after devtmpfs */
    console = open("/dev/console", O_WRONLY);
    if (console >= 0) { dup2(console, 1); dup2(console, 2); }

    putstr("ARTI Linux init: loading module...\r\n");

    /* Load the RTL smoke-test module. */
    int ret = load_module("/arti_rtl_test.ko", "", "arti_rtl_test");
    if (ret < 0) {
        putstr("ARTI LINUX INIT FAIL: cannot open .ko\r\n");
    } else {
        char buf[16];
        putstr("ARTI Linux init: finit_module returned 0x");
        putstr(u32hex((unsigned)ret, buf));
    }

    /* Prefer the DRM handoff when its dependency modules are supplied. */
    if (access("/drm.ko", F_OK) == 0) {
        load_module("/backlight.ko", "", "backlight");
        load_module("/drm.ko", "", "drm");
        load_module("/drm_kms_helper.ko", "", "drm_kms_helper");
        load_module("/drm_client_lib.ko", "", "drm_client_lib");
        load_module("/drm_shmem_helper.ko", "", "drm_shmem_helper");
        if (load_module("/arti_gpu_drm.ko", "", "arti_gpu_drm") == 0)
            putstr("ARTI Linux init: DRM driver loaded\r\n");
    } else if (load_module("/arti_gpu_probe.ko", "", "arti_gpu_probe") == 0) {
        putstr("ARTI Linux init: GPU probe loaded\r\n");
    }

    /* Give the kernel time to flush probe messages to console */
    sync();
    sleep(1);
    sync();

    putstr("ARTI Linux init: done, powering off\r\n");

    /* Power off */
    reboot(LINUX_REBOOT_CMD_POWER_OFF);

    /* If reboot fails, spin */
    for (;;) pause();
    return 0;
}
