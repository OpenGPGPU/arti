/* SPDX-License-Identifier: GPL-2.0 */
/* Minimal static init for ARTI Linux device test.
 * Mounts core filesystems, loads arti_rtl_test.ko and an optional external
 * driver, prints result,
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

static void load_external_dependencies(void) {
    char manifest[4096];
    int fd = open("/arti_driver_deps", O_RDONLY);
    ssize_t count;
    size_t start = 0;

    if (fd < 0)
        return;
    count = read(fd, manifest, sizeof(manifest) - 1);
    close(fd);
    if (count <= 0)
        return;
    manifest[count] = 0;

    for (size_t i = 0; i <= (size_t)count; i++) {
        if (manifest[i] != '\n' && manifest[i] != 0)
            continue;
        manifest[i] = 0;
        if (i > start)
            load_module(manifest + start, "", manifest + start);
        start = i + 1;
    }
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

    /* The smoke module is optional when an external driver is being tested. */
    if (access("/arti_rtl_test.ko", F_OK) == 0) {
        int ret = load_module("/arti_rtl_test.ko", "", "arti_rtl_test");
        if (ret < 0) {
            putstr("ARTI LINUX INIT FAIL: cannot load smoke .ko\r\n");
        } else {
            char buf[16];
            putstr("ARTI Linux init: finit_module returned 0x");
            putstr(u32hex((unsigned)ret, buf));
        }
    }

    /* An externally supplied driver owns the device when present. Its ABI and
     * compatible string are intentionally unknown to this generic harness. */
    int external_driver = access("/arti_driver.ko", F_OK) == 0;
    if (external_driver) {
        load_external_dependencies();
        load_module("/arti_driver.ko", "", "arti_driver");
    }

    /* Reference GPU drivers are opt-in and are only used when no external
     * driver was supplied. */
    if (!external_driver && access("/drm.ko", F_OK) == 0) {
        load_module("/backlight.ko", "", "backlight");
        load_module("/drm.ko", "", "drm");
        load_module("/drm_kms_helper.ko", "", "drm_kms_helper");
        load_module("/drm_client_lib.ko", "", "drm_client_lib");
        load_module("/drm_shmem_helper.ko", "", "drm_shmem_helper");
        if (load_module("/arti_gpu_drm.ko", "", "arti_gpu_drm") == 0)
            putstr("ARTI Linux init: DRM driver loaded\r\n");
    } else if (!external_driver && load_module("/arti_gpu_probe.ko", "", "arti_gpu_probe") == 0) {
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
