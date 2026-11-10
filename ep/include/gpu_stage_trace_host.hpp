#pragma once
#include <cstdint>
#include <string>
namespace uccl { namespace gpu_stages {
void begin(std::string const& mode);
std::string end();
void validate_launch(uintptr_t stream,unsigned gx,unsigned gy,unsigned gz,
                     unsigned bx,unsigned by,unsigned bz);
} }
