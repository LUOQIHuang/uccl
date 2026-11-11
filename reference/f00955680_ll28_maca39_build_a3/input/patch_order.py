"""Apply runtime-selectable ordering to the isolated, instrumented EP tree."""

from __future__ import annotations

import difflib
import hashlib
import pathlib
import sys


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one {description}; found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    tree = pathlib.Path(sys.argv[1])
    rdma_path = tree / "ep/src/rdma.cpp"
    urma_path = tree / "ep/src/urma_transport.cpp"
    originals = {rdma_path: rdma_path.read_text(), urma_path: urma_path.read_text()}
    expected = {
        rdma_path: "8f3f6b9ceb8563311e706e16525ab49dee2738185d2aa53aa373aa86fdd47baf",
        urma_path: "58afb9f889bb009656fc9bfb80b8dff5b28b54d27caecd99d1322490d8726bff",
    }
    for path, original in originals.items():
        if sha256(original) != expected[path]:
            raise RuntimeError(f"Unexpected source identity: {path}")

    rdma = originals[rdma_path]
    helper = r'''
// Test-only: apply the same requested ordering to the data MR probe and MR.
// Separately registered atomic and barrier buffers retain their normal flags.
static int latency_main_mr_access_flags() {
  int flags = IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE |
              IBV_ACCESS_REMOTE_ATOMIC;
  auto const* mode = std::getenv("UCCL_EP_VERBS_RELAXED_ORDERING");
  if (mode != nullptr && std::strcmp(mode, "1") == 0) {
    flags |= IBV_ACCESS_RELAXED_ORDERING;
  } else if (mode != nullptr && std::strcmp(mode, "0") != 0) {
    std::fprintf(stderr, "[LATENCY] invalid VERBS_RELAXED_ORDERING=%s\n", mode);
    std::abort();
  }
  std::fprintf(stderr, "[LATENCY] VERBS_MAIN_MR_RELAXED=%d ACCESS_FLAGS=0x%x\n",
               !!(flags & IBV_ACCESS_RELAXED_ORDERING), flags);
  return flags;
}

'''
    marker = "// Test-only receive markers."
    rdma = replace_once(rdma, marker, helper + marker, "RDMA helper insertion")
    rdma = replace_once(
        rdma,
        "  int access_flags = IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE |\n"
        "                     IBV_ACCESS_REMOTE_ATOMIC;",
        "  int access_flags = latency_main_mr_access_flags();",
        "RDMA main MR flags",
    )
    rdma = replace_once(
        rdma,
        "  int const access = IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE |\n"
        "                     IBV_ACCESS_REMOTE_ATOMIC;",
        "  int const access = latency_main_mr_access_flags();",
        "RDMA main MR probe flags",
    )

    urma = originals[urma_path]
    start = urma.index("void UrmaTransport::post_write(int dst_rank")
    end = urma.index("bool UrmaTransport::allocate_operand_slot", start)
    body = urma[start:end]
    flag = "  wr.flag.bs.place_order = URMA_STRONG_ORDER;"
    dynamic = r'''  static unsigned int const latency_write_order = [] {
    auto const* mode = std::getenv("UCCL_EP_URMA_WRITE_ORDER");
    unsigned int order = URMA_STRONG_ORDER;
    if (mode != nullptr && std::strcmp(mode, "relaxed") == 0) {
      order = URMA_RELAX_ORDER;
    } else if (mode != nullptr && std::strcmp(mode, "strong") != 0) {
      std::fprintf(stderr, "[LATENCY] invalid URMA_WRITE_ORDER=%s\n", mode);
      std::abort();
    }
    std::fprintf(stderr, "[LATENCY] URMA_WRITE_ORDER=%s\n",
                 order == URMA_RELAX_ORDER ? "relaxed" : "strong");
    return order;
  }();
  wr.flag.bs.place_order = latency_write_order;'''
    body = replace_once(body, flag, dynamic, "URMA WRITE ordering")
    urma = urma[:start] + body + urma[end:]
    if urma.count(flag) != 2:
        raise RuntimeError("FADD/barrier ordering changed unexpectedly")

    for path, updated in ((rdma_path, rdma), (urma_path, urma)):
        original = originals[path]
        print("\n".join(difflib.unified_diff(original.splitlines(), updated.splitlines(),
                                               fromfile=f"{path}.original", tofile=str(path),
                                               lineterm="")))
        print(f"PATCHED_SHA256={sha256(updated)} PATH={path}")
        path.write_text(updated)


if __name__ == "__main__":
    main()
