#include <cuda_runtime.h>
#include <cstdio>
#include <cstdlib>

#define CHECK(x) do { \
    cudaError_t e = (x); \
    if (e != cudaSuccess) { \
        std::fprintf(stderr, "%s: %s\n", #x, cudaGetErrorString(e)); \
        std::exit(1); \
    } \
} while (0)

__global__ void fill(int* p) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < 256) p[i] = i * 3 + 7;
}

int main() {
    int h[256], *d = nullptr;
    CHECK(cudaSetDevice(0));
    CHECK(cudaMalloc(reinterpret_cast<void**>(&d), sizeof(h)));
    fill<<<4, 64>>>(d);
    CHECK(cudaGetLastError());
    CHECK(cudaDeviceSynchronize());
    CHECK(cudaMemcpy(h, d, sizeof(h), cudaMemcpyDeviceToHost));
    CHECK(cudaFree(d));
    for (int i = 0; i < 256; ++i) {
        if (h[i] != i * 3 + 7) {
            std::fprintf(stderr, "FAIL index=%d value=%d\n", i, h[i]);
            return 1;
        }
    }
    std::puts("KERNEL_PASS checked=256");
    return 0;
}
