#include <mc_runtime.h>
#include <cstdio>
#include <cstdlib>

#define CHECK(x) do { \
    mcError_t e = (x); \
    if (e != mcSuccess) { \
        std::fprintf(stderr, "%s: %s\n", #x, mcGetErrorString(e)); \
        std::exit(1); \
    } \
} while (0)

__global__ void fill(int* p) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < 256) p[i] = i * 3 + 7;
}

int main() {
    int h[256], *d = nullptr;
    CHECK(mcSetDevice(0));
    CHECK(mcMalloc(reinterpret_cast<void**>(&d), sizeof(h)));
    fill<<<4, 64>>>(d);
    CHECK(mcGetLastError());
    CHECK(mcDeviceSynchronize());
    CHECK(mcMemcpy(h, d, sizeof(h), mcMemcpyDeviceToHost));
    CHECK(mcFree(d));
    for (int i = 0; i < 256; ++i) {
        if (h[i] != i * 3 + 7) {
            std::fprintf(stderr, "FAIL index=%d value=%d\n", i, h[i]);
            return 1;
        }
    }
    std::puts("KERNEL_PASS checked=256");
    return 0;
}
