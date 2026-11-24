// SPDX-License-Identifier: MIT
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "ummu_log.h"
#include "ummu_resource.h"
#include "ummu_queue.h"

#define MAX_WAIT_NOT_FULL_TIMES 300
#define WAIT_NOT_FULL_INTERVAL_US 25
#define WAIT_NOT_FULL_DIVIDEND 5
#define WAIT_CPLQ_TIMES 4
#define WAIT_CPLQ_INTERVAL_US 2

int ummu_queue_create(struct ummu_mapt_info *mapt_info, struct ummu_tid_info *info, int fd)
{
	struct ummu_mapt_queue_args *queue;
	size_t queue_size, size;
	uint32_t ummu_cnt;
	void *start_addr;
	uint32_t i;
	int ret;

	if (mapt_info->kcmd_plbi) {
	    UMMU_MAPT_DEBUG_LOG("ummu perm queue uses kcmd, not the user mode queue.\n");
	    return 0;
	}

	ummu_cnt = info->ummu_cnt;
	queue = (struct ummu_mapt_queue_args *)malloc(ummu_cnt * sizeof(struct ummu_mapt_queue_args));
	if (queue == NULL) {
		UMMU_MAPT_ERROR_LOG("Alloc mapt queue args failed.\n");
		ret = -ENOMEM;
		goto err_malloc_queue_arg;
	}

	size = PAGE_SIZE * (1UL + (1UL << info->pcmdq_order) + (1UL << info->pcplq_order));
	queue_size = size * ummu_cnt;
	start_addr = ummu_get_core_buf(mapt_info->tid, BASE_MODE_QUEUE, queue_size, fd, 0);
	if (CORE_BUF_CHECK_INVALID(start_addr)) {
		UMMU_MAPT_ERROR_LOG("Get queue resource failed.\n");
		ret = -ENOMEM;
		goto err_mmap_queue_buffer;
	}

	for (i = 0; i < ummu_cnt; i++) {
		queue[i].cmd_entry = (struct ummu_mapt_cmd_entry *)start_addr;
		queue[i].cpl_entry = (struct ummu_mapt_cpl_entry *)(start_addr + PAGE_SIZE *
					(1UL << info->pcmdq_order));
		queue[i].ctrl_page = (struct ummu_mapt_ctrl_pages *)(start_addr + PAGE_SIZE *
					((1UL << info->pcmdq_order) + (1U << info->pcplq_order)));
		start_addr += size;
	}

	mapt_info->queue_ctx.queue = queue;
	mapt_info->queue_ctx.ummu_cnt = info->ummu_cnt;
	mapt_info->queue_ctx.queue_size = queue_size;
	mapt_info->queue_ctx.cmd_que_pi_size = info->pcmdq_order + (uint8_t)PAGE_SHIFT - (uint8_t)CMD_ENTRY_SHIFT;
	mapt_info->queue_ctx.cpl_que_size = info->pcplq_order + (uint8_t)PAGE_SHIFT - (uint8_t)CPL_ENTRY_SHIFT;

	return 0;
err_mmap_queue_buffer:
	free(queue);
err_malloc_queue_arg:
	return ret;
}

int ummu_send_cmd(struct ummu_mapt_cmd_entry *cmd_plbi, struct ummu_mapt_info *mapt_info, uint32_t ummu_index)
{
	struct ummu_mapt_cmd_entry cmd_psync = {.opcode = PSYNC_OP, .cqe_en = 1};
	struct ummu_mapt_queue_args *cmd_que = &mapt_info->queue_ctx.queue[ummu_index];
	struct ummu_mapt_ctrl_pages *ctrl_page = cmd_que->ctrl_page;
	uint32_t plbi_num = mapt_info->double_plbi ? 2 : 1;
	uint16_t wait_interval = WAIT_NOT_FULL_INTERVAL_US;
	uint16_t times = MAX_WAIT_NOT_FULL_TIMES;
	uint16_t pi_wr, pi_sync;
	int ret;

	while (queue_has_space(ctrl_page->ucmdq_pi, ctrl_page->ucmdq_ci,
				mapt_info->queue_ctx.cmd_que_pi_size, CMD_NUM_PER_PLBI * plbi_num) == 0) {
		/* queue is full, should wait for available index. */
		(void)usleep(wait_interval);
		if (times == 0) {
			UMMU_MAPT_ERROR_LOG("Timeout, command queue is still full, plbi_num = %u.\n", plbi_num);
			return -ETIMEDOUT;
		}
		times--;
		wait_interval = WAIT_NOT_FULL_INTERVAL_US *
			((MAX_WAIT_NOT_FULL_TIMES - times) % WAIT_NOT_FULL_DIVIDEND);
	}

	pi_wr = cmd_que->ctrl_page->ucmdq_pi;
	for (uint32_t idx = 0; idx < plbi_num; idx++) {
		(void)memcpy((struct ummu_mapt_cmd_entry *)cmd_que->cmd_entry +
				Q_IDX(pi_wr, mapt_info->queue_ctx.cmd_que_pi_size), cmd_plbi,
				sizeof(struct ummu_mapt_cmd_entry));
		pi_wr = ummu_update_queue_index(pi_wr, mapt_info->queue_ctx.cmd_que_pi_size);
		pi_sync = pi_wr;
		(void)memcpy((struct ummu_mapt_cmd_entry *)cmd_que->cmd_entry +
				Q_IDX(pi_wr, mapt_info->queue_ctx.cmd_que_pi_size), &cmd_psync,
				sizeof(struct ummu_mapt_cmd_entry));
		pi_wr = ummu_update_queue_index(pi_wr, mapt_info->queue_ctx.cmd_que_pi_size);
	}

	ummu_to_device_wmb();
	/* write prod to hardware */
	cmd_que->ctrl_page->ucmdq_pi = pi_wr;
	times = WAIT_CPLQ_TIMES;
	while (times > 0) {
		ret = ummu_get_cpl_psync(mapt_info, cmd_que, pi_sync);
		if (!ret) {
			break;
		}
		times--;
		if (times == 0) {
			UMMU_MAPT_INFO_LOG("Wait psync complete timeout ret = %d.\n", ret);
			break;
		}
		(void)usleep(WAIT_CPLQ_INTERVAL_US * (WAIT_CPLQ_TIMES - times));
	}

	UMMU_MAPT_DEBUG_LOG("ummu_index = %u, opcode = 0x%02x, cqe_en = %u, plbi_num = %u.\n", ummu_index,
				cmd_psync.opcode, (uint32_t)cmd_psync.cqe_en, plbi_num);
	return 0;
}

static uint32_t get_minist_log2size_range(size_t size)
{
	uint32_t idx = 0;
	uint64_t temp = 0;

	if (size > 0) {
		temp = (uint64_t)size - (uint64_t)1;
	}

	while (temp > 0) {
		temp >>= 1;
		idx++;
	}

	return idx;
}

static void ummu_send_plbi(struct ummu_mapt_cmd_entry *cmd_plbi, struct ummu_mapt_info *mapt_info,
	struct ummu_tid_info *info)
{
	struct ummu_ctx_info *ummu_ctx = get_ummu_ctx();

	if (!mapt_info->kcmd_plbi) {
		for (uint32_t i = 0; i < mapt_info->queue_ctx.ummu_cnt; i++) {
			(void)ummu_send_cmd(cmd_plbi, mapt_info, i);
		}

		return;
	}

	ummu_kcmd_plbi(ummu_ctx->shared_fd, info, cmd_plbi->opcode);
}

void ummu_plbi_va_cmd(struct ummu_mapt_info *mapt_info, uint64_t va, uint64_t size)
{
	struct ummu_mapt_cmd_entry cmd_va_entry = { 0 };
	struct ummu_tid_info info = {.tid = mapt_info->tid,
				     .va = va,
				     .size = size};

	cmd_va_entry.opcode = PLBI_VA_OP;
	cmd_va_entry.range = get_minist_log2size_range(size);
	cmd_va_entry.addr = va;

	ummu_send_plbi(&cmd_va_entry, mapt_info, &info);
}

void ummu_plbi_f_bit_cmd(struct ummu_mapt_info *mapt_info, uint32_t next_lvl_idx, uint32_t next_lvl_offset)
{
	struct ummu_mapt_cmd_free_bit_entry cmd_entry = { 0 };
	struct ummu_tid_info info = {.tid = mapt_info->tid,
				     .va = 0,
				     .size = 0};

	cmd_entry.opcode = PLBI_USR_N;
	cmd_entry.next_lvl_idx = next_lvl_idx;
	cmd_entry.next_lvl_offset = next_lvl_offset;

	ummu_send_plbi((struct ummu_mapt_cmd_entry *)&cmd_entry, mapt_info, &info);
}

void ummu_send_user_all(struct ummu_mapt_info *mapt_info)
{
	struct ummu_mapt_cmd_entry cmd_usr_all_entry = {.opcode = PLBI_ALL_OP};
	struct ummu_tid_info info = {.tid = mapt_info->tid};

	ummu_send_plbi(&cmd_usr_all_entry, mapt_info, &info);
}

void ummu_queue_destroy(struct ummu_mapt_info *mapt_info)
{
	ummu_send_user_all(mapt_info);

	if (mapt_info->kcmd_plbi)
	    return;

	ummu_free_core_buf(BASE_MODE_QUEUE, (void *)mapt_info->queue_ctx.queue[0].cmd_entry,
				mapt_info->queue_ctx.queue_size);
	free(mapt_info->queue_ctx.queue);
	mapt_info->queue_ctx.queue = NULL;
}

bool queue_has_space(uint16_t pi, uint16_t ci, uint16_t cmd_que_size, uint16_t n)
{
	uint16_t space;
	uint16_t ci_rd, pi_wr;

	ci_rd = Q_IDX(ci, cmd_que_size);
	pi_wr = Q_IDX(pi, cmd_que_size);

	if (Q_WRAP(pi, cmd_que_size) == Q_WRAP(ci, cmd_que_size)) {
		space = ((uint16_t)1 << cmd_que_size) - (pi_wr - ci_rd);
	} else {
		space = ci_rd - pi_wr;
	}

	return space >= n;
}

uint16_t ummu_update_queue_index(uint16_t index, uint16_t que_size)
{
	uint16_t idx, idx_wrap;
	uint16_t max_que;

	max_que = (uint16_t)MAX_CMD_QUE_INDEX(que_size);
	idx = index & max_que;
	idx_wrap = index >> que_size;

	if (idx == max_que) {
		idx_wrap ^= 1;
		idx = 0;
	} else {
		idx += 1;
	}

	return (idx_wrap << que_size) | idx;
}

int ummu_get_cpl_psync(struct ummu_mapt_info *mapt_info, struct ummu_mapt_queue_args *cmd_que, uint16_t pi_sync)
{
	struct ummu_mapt_cpl_entry *cur_cpl_entry;
	uint16_t pi, ci, idx_mask;
	int ret = -EAGAIN;

	pi = cmd_que->ctrl_page->ucplq_pi;
	ci = cmd_que->ctrl_page->ucplq_ci;
	ummu_from_device_rmb();
	idx_mask = (uint16_t)MAX_CMD_QUE_INDEX(mapt_info->queue_ctx.cpl_que_size);

	/* exit with error code when cpl queue is empty */
	if (pi == ci) {
		return -EINVAL;
	}

	/* process the cpl entry until the expected one */
	while (pi != ci) {
		cur_cpl_entry = cmd_que->cpl_entry + (ci & idx_mask);
		if (cur_cpl_entry->cpl_status == CPL_CMD_PSYNC_SUCC) {
			UMMU_MAPT_DEBUG_LOG("CMD_PSYNC Success! CMD_Queue_CI = %u.\n", cur_cpl_entry->cmdq_ci);
		} else if (cur_cpl_entry->cpl_status == CPL_CMD_TYPE_ERROR) {
			UMMU_MAPT_ERROR_LOG("CMD type Error! CMD Queue CI = %u.\n", cur_cpl_entry->cmdq_ci);
		} else if (cur_cpl_entry->cpl_status == CPL_CMD_PROC_ERROR) {
			UMMU_MAPT_ERROR_LOG("CMD process Error! CMD Queue CI = %u.\n", cur_cpl_entry->cmdq_ci);
		} else {
			UMMU_MAPT_ERROR_LOG("CPL Status Error! CPL Queue PI = %u.\n", cmd_que->ctrl_page->ucplq_pi);
		}

		ci = ummu_update_queue_index(ci, mapt_info->queue_ctx.cpl_que_size);
		if (cur_cpl_entry->cmdq_ci == pi_sync) {
			ret = 0;
			break;
		}
	}

	cmd_que->ctrl_page->ucplq_ci = ci;

	return ret;
}

