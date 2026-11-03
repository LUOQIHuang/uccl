#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
typedef int (*grant_fn)(uint32_t, void *, size_t, int, void *);
int ummu_grant(uint32_t tid, void *address, size_t bytes, int permission, void *attrs) {
  grant_fn real_grant = (grant_fn)dlsym(RTLD_NEXT, "ummu_grant");
  if (!real_grant) { fprintf(stderr, "WRAP_ERROR ummu_grant missing\n"); abort(); }
  int ret=real_grant(tid,address,bytes,permission,attrs);
  long page=sysconf(_SC_PAGESIZE);
  fprintf(stderr,"GRANT_TRACE offset=%llu len=%zu perm=%d rc=%d\n",
          (unsigned long long)((uintptr_t)address % (uintptr_t)page),bytes,permission,ret);
  return ret;
}
