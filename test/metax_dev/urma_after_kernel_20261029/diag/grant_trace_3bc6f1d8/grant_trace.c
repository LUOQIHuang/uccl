#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ummu_api.h>
#include "urma_api.h"
#include "urma_provider.h"

static const char *object_path(void *function)
{
    Dl_info info;
    return dladdr(function, &info) && info.dli_fname ? info.dli_fname : "UNKNOWN";
}

static int find_loaded_ummu(struct dl_phdr_info *info, size_t size, void *out)
{
    (void)size;
    const char *name = strrchr(info->dlpi_name, '/');
    name = name ? name + 1 : info->dlpi_name;
    if (strncmp(name, "libummu.so", 10) != 0)
        return 0;
    /* The provider loads this dependency locally. Observe the existing object;
       do not introduce a second library or change its visibility. */
    void *handle = dlopen(info->dlpi_name, RTLD_NOW | RTLD_NOLOAD);
    if (!handle)
        return 0;
    *(void **)out = dlsym(handle, "ummu_grant");
    dlclose(handle);
    return *(void **)out != NULL;
}

int ummu_grant(uint32_t tid, void *data, size_t data_size,
               enum ummu_mapt_perm perm, struct ummu_seg_attr *attr)
{
    typedef int (*function_t)(uint32_t, void *, size_t,
                             enum ummu_mapt_perm, struct ummu_seg_attr *);
    function_t real = (function_t)dlsym(RTLD_NEXT, "ummu_grant");
    if (!real) {
        void *resolved = NULL;
        dl_iterate_phdr(find_loaded_ummu, &resolved);
        real = (function_t)resolved;
    }
    if (!real) {
        fprintf(stderr, "GRANT_TRACE_RESOLVE_FAILED\n");
        abort();
    }
    int saved_errno = errno;
    fprintf(stderr, "GRANT_CALL tid=%u va=%p len=%zu perm=%u atomic_rw=%u e_bit=%d library=%s\n",
            tid, data, data_size, (unsigned)perm, (unsigned)MAPT_PERM_ATOMIC_RW,
            attr ? (int)attr->e_bit : -1, object_path((void *)real));
    errno = saved_errno;
    int rc = real(tid, data, data_size, perm, attr);
    saved_errno = errno;
    fprintf(stderr, "GRANT_RETURN tid=%u va=%p rc=%d\n", tid, data, rc);
    errno = saved_errno;
    return rc;
}

urma_target_seg_t *urma_register_seg(urma_context_t *ctx, urma_seg_cfg_t *cfg)
{
    typedef urma_target_seg_t *(*function_t)(urma_context_t *, urma_seg_cfg_t *);
    function_t real = (function_t)dlsym(RTLD_NEXT, "urma_register_seg");
    if (!real) {
        fprintf(stderr, "REGISTER_TRACE_RESOLVE_FAILED\n");
        abort();
    }
    int saved_errno = errno;
    fprintf(stderr, "REGISTER_PATH va=%" PRIx64 " len=%" PRIu64 " access=%u gpu=%u register_provider=%s send_provider=%s\n",
            cfg->va, cfg->len, cfg->flag.bs.access, cfg->is_gpu_seg,
            object_path((void *)ctx->ops->register_seg),
            object_path((void *)ctx->ops->post_jetty_send_wr));
    errno = saved_errno;
    urma_target_seg_t *result = real(ctx, cfg);
    saved_errno = errno;
    fprintf(stderr, "REGISTER_RETURN va=%" PRIx64 " ok=%d token_id=%u\n",
            cfg->va, result != NULL, result ? result->seg.token_id : 0);
    errno = saved_errno;
    return result;
}
