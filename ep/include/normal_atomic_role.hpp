#pragma once
#include <cstdint>
// Local GPU->CPU command annotation only. Atomic requests do not otherwise
// consume TransferCmd::atomic_offset. No remote address/value changes.
enum class NormalAtomicRole : uint16_t { Unknown=0, Tail=1, Head=2 };
