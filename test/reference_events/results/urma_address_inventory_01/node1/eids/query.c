
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <urma_api.h>
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    Dl_info library = {0};
    /* Resolve the implementation, since &urma_init may denote our PLT stub. */
    void *implementation = dlsym(RTLD_DEFAULT, "urma_init");
    if (!implementation || !dladdr(implementation, &library)) return 3;
    printf("URMA_QUERY_LIBRARY path=%s\n", library.dli_fname);
    int status = urma_init(NULL);
    if (status != 0) { fprintf(stderr, "urma_init status=%d\n", status); return 4; }
    int count = 0, selected = 0, valid = 0;
    urma_device_t **devices = urma_get_device_list(&count);
    if (devices) {
        for (int i = 0; i < count; ++i) {
            if (!devices[i]) continue;
            printf("URMA_QUERY_DEVICE name=%s\n", devices[i]->name);
            if (strcmp(devices[i]->name, argv[1])) continue;
            selected = 1;
            uint32_t n = 0;
            urma_eid_info_t *eids = urma_get_eid_list(devices[i], &n);
            if (eids) {
                for (uint32_t k = 0; k < n; ++k) {
                    printf("URMA_QUERY_EID device=%s position=%u index=%u eid=", argv[1], k, eids[k].eid_index);
                    for (unsigned b = 0; b < sizeof(eids[k].eid.raw); ++b) printf("%02x", eids[k].eid.raw[b]);
                    printf("\n");
                    valid = 1;
                }
                urma_free_eid_list(eids);
            }
        }
        urma_free_device_list(devices);
    }
    FILE *maps = fopen("/proc/self/maps", "r");
    if (maps) {
        char line[4096];
        while (fgets(line, sizeof(line), maps)) {
            char *path = strchr(line, '/');
            if (path && strstr(path, ".so")) printf("URMA_QUERY_MAP path=%s", path);
        }
        fclose(maps);
    }
    status = urma_uninit();
    return !selected ? 5 : !valid ? 6 : status != 0 ? 7 : 0;
}
