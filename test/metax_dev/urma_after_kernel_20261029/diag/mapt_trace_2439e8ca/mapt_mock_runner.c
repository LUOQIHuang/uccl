#include <dlfcn.h>
#include <stdint.h>
int main(void) {
    void *h = dlopen("libmockprovider.so", RTLD_NOW | RTLD_LOCAL);
    if (!h) return 1;
    int (*call)(uint32_t) = (int (*)(uint32_t))dlsym(h, "mock_call");
    if (!call || call(42) || call(43)) return 2;
    dlclose(h);
    return 0;
}
