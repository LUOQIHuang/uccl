/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_QUEUE_H_
#define _UMMU_QUEUE_H_

#include <stdbool.h>

#include "ummu_mapt.h"
#include "ummu_common.h"

#define PAGE_SIZE ((unsigned long)(getpagesize()))

#define PAGE_SHIFT ((unsigned long)(((PAGE_SIZE / 0x1000UL) > 0UL) ? \
			(12UL + (unsigned long)__builtin_ctz(PAGE_SIZE / 0x1000UL)) : 12UL))


#define PSYNC_OP 0x01

#define PLBI_ALL_OP 0x10

#define PLBI_VA_OP 0x11

#define PLBI_USR_N 0x12

#define CPL_CMD_PSYNC_SUCC 0b0001

#define CPL_CMD_TYPE_ERROR 0b0010

#define CPL_CMD_PROC_ERROR 0b0011
#define CMD_NUM_PER_PLBI 2
#define Q_WRAP(idx, size) ((idx) & ((uint16_t)1 << (size)))
#define Q_IDX(idx, size) ((idx) & (((uint16_t)1 << (size)) - (uint16_t)1))

struct ummu_mapt_cmd_entry {
	uint32_t opcode : 8;
	uint32_t cqe_en : 1;
	uint32_t reserved_0 : 23;
	uint32_t range : 6;
	uint32_t reserved_1 : 26;
	uint64_t addr;
};

struct ummu_mapt_cmd_free_bit_entry {
	uint32_t opcode : 8;
	uint32_t reserved_0 : 24;
	uint32_t reserved_1;
	uint32_t next_lvl_idx : 16;
	uint32_t reserved_2 : 16;
	uint32_t next_lvl_offset : 30;
	uint32_t reserved_3 : 2;
};

#define CMD_ENTRY_SHIFT 4

#define CPL_ENTRY_SHIFT 2

struct ummu_mapt_cpl_entry {
	uint32_t cpl_status : 4;
	uint32_t reserved_0 : 12;
	uint32_t cmdq_ci : 16;
};

struct ummu_mapt_ctrl_pages {
	uint16_t ucmdq_pi;
	uint16_t reserved_0;
	uint16_t ucmdq_ci;
	uint16_t reserved_1;
	uint16_t ucplq_pi;
	uint16_t reserved_2;
	uint16_t ucplq_ci;
	uint16_t reserved_3;
};

struct ummu_mapt_queue_args {
	struct ummu_mapt_cmd_entry *cmd_entry;
	struct ummu_mapt_cpl_entry *cpl_entry;
	struct ummu_mapt_ctrl_pages *ctrl_page;
};

int ummu_queue_create(struct ummu_mapt_info *mapt_info, struct ummu_tid_info *info, int fd);
int ummu_send_cmd(struct ummu_mapt_cmd_entry *cmd_plbi, struct ummu_mapt_info *mapt_info, uint32_t ummu_index);
void ummu_plbi_va_cmd(struct ummu_mapt_info *mapt_info, uint64_t va, uint64_t size);
void ummu_plbi_f_bit_cmd(struct ummu_mapt_info *mapt_info, uint32_t next_lvl_idx, uint32_t next_lvl_offset);
void ummu_send_user_all(struct ummu_mapt_info *mapt_info);
void ummu_queue_destroy(struct ummu_mapt_info *mapt_info);
bool queue_has_space(uint16_t pi, uint16_t ci, uint16_t cmd_que_size, uint16_t n);
uint16_t ummu_update_queue_index(uint16_t index, uint16_t que_size);
int ummu_get_cpl_psync(struct ummu_mapt_info *mapt_info, struct ummu_mapt_queue_args *cmd_que, uint16_t pi_sync);

#endif

