// Test fixture only. Production builds MUST use the server's UMDK headers.
// Models the fields used in tests; this is not a vendor ABI definition.
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef enum { URMA_SUCCESS=0, URMA_EINVAL=22 } urma_status_t;
enum { URMA_OPC_WRITE=1, URMA_OPC_FADD=2, URMA_RELAX_ORDER=0, URMA_STRONG_ORDER=1, URMA_CR_SUCCESS=0 };
typedef struct {} urma_jetty_t;
typedef struct {} urma_jfc_t;
typedef struct {} urma_context_t;
typedef struct {} urma_target_seg_t;
typedef struct { uint64_t addr,len; urma_target_seg_t* tseg; } urma_sge_t;
typedef struct { urma_sge_t* sge; uint32_t num_sge; } mock_sgl;
typedef struct urma_jfs_wr {
  struct urma_jfs_wr* next;
  int opcode;
  uint64_t user_ctx;
  struct { struct { unsigned complete_enable,place_order,fence; } bs; } flag;
  struct { mock_sgl src,dst; } rw;
} urma_jfs_wr_t;
typedef struct { uint64_t user_ctx; int status; } urma_cr_t;
typedef struct { uint64_t va,len; unsigned is_gpu_seg; struct { struct { unsigned access; } bs; } flag; } urma_seg_cfg_t;
urma_status_t urma_post_jetty_send_wr(urma_jetty_t*,urma_jfs_wr_t*,urma_jfs_wr_t**);
int urma_poll_jfc(urma_jfc_t*,int,urma_cr_t*);
urma_target_seg_t* urma_register_seg(urma_context_t*,urma_seg_cfg_t*);
#ifdef __cplusplus
}
#endif
