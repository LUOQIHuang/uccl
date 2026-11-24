/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_MAPT_H_
#define _UMMU_MAPT_H_

#include <stdint.h>
#include <pthread.h>
#include "ummu_api.h"

/* =============================================== common data structure ======================================== */
struct ummu_mapt_entry_node {
	uint32_t valid : 1;
	uint32_t reserved_0 : 2;
	uint32_t e_bit : 1;
	uint32_t permission : 6;
	uint32_t reserved_1 : 22;

	uint32_t reserved_2;

	uint32_t base_low;
	uint32_t base_high : 16;
	uint32_t reserved_3 : 12;
	uint32_t reserved_4 : 3;
	uint32_t token_check : 1;

	uint32_t limit_low;
	uint32_t limit_high : 16;
	uint32_t reserved_5 : 12;
	uint32_t reserved_6 : 2;
	uint32_t nonce : 2;

	uint32_t token_val_0;
	uint32_t token_val_1;
};

#define MAX_LEVEL_INDEX 3U

/* (64K / (sizeof(ummu_mapt_table_node) * 512)) */
#define PER_MAPT_LEVEL_BLOCK_CNT 4U

#define INDEX_MAX_SIZE 512UL

#define MAX_MAPT_ENTRY_INDEX 512U

#define MAX_LEVEL_ID_SIZE (INDEX_MAX_SIZE * PER_MAPT_LEVEL_BLOCK_CNT)

#define BITS_TO_BITMAP_SHIFT 6U

#define INDEX_LEVEL_BITMAP_SIZE (MAX_LEVEL_ID_SIZE >> BITS_TO_BITMAP_SHIFT)

#define INVALID_ADDR 0xFFF

/* max address = 0x1 000 000 000 000 - 1 */
#define MAX_ADDRESS_BITS 48U
#define UMMU_MAX_TOKEN_NUM 2

struct ummu_mapt_block {
	void *block_addr;
	size_t blk_size;
	uint32_t block_id;
	uint16_t level_cnt;
	uint16_t level_entry_cnt[PER_MAPT_LEVEL_BLOCK_CNT];
};

struct ummu_mapt_table_ctx {
	int expan;
	uint16_t block_cnt;
	size_t blk_exp_size;
	uint64_t granted_addr_mng;
	unsigned long level_block_bitmap[INDEX_LEVEL_BITMAP_SIZE];

	struct ummu_mapt_block *mapt_block_base;
	void *mapt_block_array[INDEX_MAX_SIZE];
};

struct ummu_mapt_queue_ctx {
	uint32_t ummu_cnt;
	size_t queue_size;
	uint16_t cmd_que_pi_size;
	uint16_t cpl_que_size;

	struct ummu_mapt_queue_args *queue;
};

struct ummu_seg_info {
	uint64_t start_addr;
	uint64_t grant_size;
	enum ummu_mapt_perm permission;
	enum ummu_ebit_state e_bit;
	/* true: check token  false: not check token */
	uint8_t token_check;
	uint8_t token_count;
	uint32_t token_val[UMMU_MAX_TOKEN_NUM];
};

struct ummu_mapt_info {
	union {
		struct ummu_mapt_entry_node *entry_block;
		struct ummu_mapt_table_ctx *table_ctx;
	} block_base;

	unsigned int tid;
	uint16_t valid;
	uint8_t double_plbi;
	uint8_t kcmd_plbi;
	uint8_t positive_plbi;
	uint8_t free_bit;
	enum ummu_mapt_mode mode;
	/* Notice: when using mapt_mutex, you must ensure that g_ummu_ctx->ctx_mutex is already locked. */
	pthread_mutex_t mapt_mutex;

	struct ummu_mapt_queue_ctx queue_ctx;
};

struct ummu_ctx_info {
	pthread_mutex_t ctx_mutex;
	void *mapt_map;
	int shared_fd;
};

enum ummu_buf_mode {
	BASE_MODE_ENTRY_BLOCK,
	BASE_MODE_TABLE_BLOCK,
	BASE_MODE_QUEUE
};

enum ummu_grant_op_type {
	UMMU_GRANT = 0,
	UMMU_ADD_TOKEN = 1,
	UMMU_REMOVE_TOKEN = 2,
	UMMU_UNGRANT = 3,
	UMMU_OP_END
};

struct ummu_mapt_table_node {
	uint32_t valid : 1;
	uint32_t type : 1;
	uint32_t next_block : 1;
	uint32_t e_bit : 1;
	uint32_t permission : 6;
	uint32_t f_bit : 1;
	uint32_t reserved_0 : 1;
	uint32_t next_lv_offset_low : 20;

	uint32_t next_lv_offset_high : 10;
	uint32_t reserved_1 : 6;
	uint32_t next_lv_index : 16;

	uint32_t base_low;
	uint32_t base_high : 7;
	uint32_t reserved_2 : 21;
	uint32_t reserved_3 : 3;
	uint32_t token_check : 1;

	uint32_t limit_low;
	uint32_t limit_high : 7;
	uint32_t reserved_4 : 21;
	uint32_t flag : 2;
	uint32_t nonce : 2;

	uint32_t token_val_0;
	uint32_t token_val_1;
};

struct ummu_data_info {
	void *data;
	size_t data_size;
	enum ummu_mapt_perm perm;
	struct ummu_token_info *token;
	uint32_t tokenval;
	uint64_t data_base;
	uint64_t data_limit;
	uint32_t token_check;
	int bytoken;
	enum ummu_ebit_state e_bit;
	enum ummu_grant_op_type op;
	struct ummu_mapt_info *mapt_info;
	uint8_t lvl;
	uint8_t head_flag;
};

#define MIN(x, y) ((x) < (y) ? (x) : (y))
#define MAPT_VPAGE_SHIFT 12U

static const uint32_t g_mapt_range_bits[MAX_LEVEL_INDEX + 1U][2] = {{47, 39}, {38, 30}, {29, 21}, {20, 12}};

#define GET_BITS_MASK(bits) (((uint64_t)1 << (bits)) - 1)

#define GET_LEVEL_RANGE_MASK(level) (GET_BITS_MASK(g_mapt_range_bits[(level)][1]))

#define GET_LEVEL_BLOCK_INDEX(addr, level) (((uint64_t)(addr) >> g_mapt_range_bits[(level)][1]) & \
		(GET_BITS_MASK(g_mapt_range_bits[(level)][0] - g_mapt_range_bits[(level)][1] + 1)))

#define MAX_CMD_QUE_INDEX(size) (((uint64_t)1 << (size)) - (uint64_t)1)

#define ALIGN_MAPT_MASK(x, mask) ((x) & ~(mask))

#define ALIGN_MAPT(x, a) ALIGN_MAPT_MASK(x, (typeof(x))(a) - 1UL)

#define ALIGN_CHECK(x, a) ((ALIGN_MAPT((x), (a))) == (x) ? true : false)

#define ALIGN_VPAGE_SHIFT_CHECK(val) ALIGN_CHECK(val, 1UL << MAPT_VPAGE_SHIFT)

#define ENTRY_ADDR_LOW(addr) ((uint32_t)((addr) & GENMASK_ULL(31, 0)))
#define ENTRY_ADDR_HIGH(addr) ((uint32_t)(((addr) & GENMASK_ULL(47, 32)) >> 32UL))

#define TABLE_ADDR_LOW(addr) ((uint32_t)((addr) & GENMASK_ULL(31, 0)))
#define TABLE_ADDR_HIGH(addr) ((uint32_t)(((addr) & GENMASK_ULL(38, 32)) >> 32UL))
#define LVL_OFFSET_LOW(offset) ((uint32_t)((offset) & GENMASK_ULL(19, 0)))
#define LVL_OFFSET_HIGH(offset) ((uint32_t)(((offset) & GENMASK_ULL(29, 20)) >> 20UL))

#define ADDR_FULL(low, high) (((uint64_t)(high) << 32UL) | (uint64_t)(low))
#define TABLE_LVL_OFFSET(low, high) (((uint32_t)(high) << 20UL) | (uint32_t)(low))

#define IS_RTE_IN_USE(addr) (((((uint64_t)(addr)) & INVALID_ADDR) == INVALID_ADDR) ? false : true)
/* =========================================== common global variable ============================================ */
extern struct ummu_ctx_info *get_ummu_ctx(void);
void ummu_mapt_destroy(struct ummu_mapt_info *info);

#endif

