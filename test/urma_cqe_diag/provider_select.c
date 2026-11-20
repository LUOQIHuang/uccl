#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
#include "urma_api.h"
#include "urma_provider.h"

/* Match the real SDK at compile time. Resolve only enumeration APIs so this
 * probe never allocates a GPU buffer, Jetty or completion queue. */
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!library) { fprintf(stderr, "dlopen: %s\n", dlerror()); return 3; }
    urma_status_t (*init)(urma_init_attr_t *) = dlsym(library, "urma_init");
    urma_status_t (*uninit)(void) = dlsym(library, "urma_uninit");
    urma_device_t **(*devices)(int *) = dlsym(library, "urma_get_device_list");
    void (*free_devices)(urma_device_t **) = dlsym(library, "urma_free_device_list");
    if (!init || !uninit || !devices || !free_devices) { dlclose(library); return 4; }
    int rc = init(NULL);
    printf("init_rc=%d\n", rc);
    if (rc) { dlclose(library); return 5; }
    int count = 0, found = 0;
    urma_device_t **list = devices(&count);
    printf("device_count=%d\n", count);
    for (int i = 0; list && i < count; ++i) {
        urma_device_t *device = list[i];
        if (!device || strcmp(device->name, argv[2])) continue;
        Dl_info info = {0};
        if (device->ops && device->ops->create_context &&
            dladdr((void *)device->ops->create_context, &info) && info.dli_fname) {
            printf("device=%s\nprovider_path=%s\n", device->name, info.dli_fname);
            found = 1;
        }
    }
    if (list) free_devices(list);
    int cleanup = uninit();
    printf("uninit_rc=%d\n", cleanup);
    dlclose(library);
    return found && !cleanup ? 0 : 6;
}
