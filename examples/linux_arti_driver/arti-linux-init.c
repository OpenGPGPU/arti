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

    /* Load the kernel module */
    int fd = open("/arti_rtl_test.ko", O_RDONLY);
    if (fd < 0) {
        putstr("ARTI LINUX INIT FAIL: cannot open .ko\r\n");
    } else {
        int ret = syscall(__NR_finit_module, fd, "", 0);
        close(fd);
        char buf[16];
        putstr("ARTI Linux init: finit_module returned 0x");
        putstr(u32hex((unsigned)ret, buf));
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
