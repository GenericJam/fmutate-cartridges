/* SPDX-License-Identifier: GPL-3.0-only */
/* native_catalog generic shim (tried when a plain build fails): <libproc.h> is macOS-only, and host code meant for a
 * Mac (X0X's x0x_host.c and its descendants: proc_pid_rusage for an instruction counter) also builds for iOS, whose
 * SDK has no libproc.h and no public proc_pid_rusage. Where the real header exists it is used; elsewhere the call
 * fails (ENOTSUP), as the firmware's code handles (its counter reads 0). */
#if __has_include_next(<libproc.h>)
#include_next <libproc.h>
#else
#ifndef FM1_SHIM_LIBPROC_H
#define FM1_SHIM_LIBPROC_H
#include <errno.h>
#include <sys/resource.h>
static inline int proc_pid_rusage(int pid, int flavor, rusage_info_t *buffer)
{
    (void)pid;
    (void)flavor;
    (void)buffer;
    errno = ENOTSUP;
    return -1;
}
#endif
#endif
