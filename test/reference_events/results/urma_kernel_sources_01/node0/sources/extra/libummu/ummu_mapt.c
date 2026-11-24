// SPDX-License-Identifier: MIT
/*
* Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
* THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
* EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
* MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
*/

#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "ummu_map.h"
#include "ummu_resource.h"
#include "ummu_queue.h"
#include "ummu_api.h"
#include "ummu_log.h"
#include "ummu_level_block.h"
#include "ummu_common.h"
#include "ummu_seg_mng.h"
#include "ummu_mapt.h"

static pthread_mutex_t g_random_mutex = PTHREAD_MUTEX_INITIALIZER;

static int ummu_mapt_entry_init(struct ummu_mapt_info *mapt_info)
{
	struct ummu_mapt_entry_node *entry_node;

	entry_node = (struct ummu_mapt_entry_node *)ummu_get_core_buf(mapt_info->tid,
		BASE_MODE_ENTRY_BLOCK, BLOCK_SIZE_4K, get_ummu_ctx()->shared_fd, 0);
	if (CORE_BUF_CHECK_INVALID(entry_node)) {
		UMMU_MAPT_ERROR_LOG("Entry node is invalid.\n");
		return -ENOMEM;
	}
	entry_node->valid = 0;
	entry_node->nonce = 0;
	mapt_info->block_base.entry_block = entry_node;

	return 0;
}

static int ummu_mapt_table_ctx_init(struct ummu_mapt_info *mapt_info, struct ummu_tid_info *info)
{
	struct ummu_mapt_table_ctx *table_ctx;
	struct ummu_mapt_block *block = NULL;
	struct ummu_mapt_table_node pre_node;
	struct ummu_mapt_table_node *root;
	int ret;

	table_ctx = (struct ummu_mapt_table_ctx *)malloc(sizeof(struct ummu_mapt_table_ctx));
	if (table_ctx == NULL) {
		UMMU_MAPT_ERROR_LOG("Alloc table_ctx memory failed.\n");
		ret = -ENOMEM;
		goto err_malloc_table_ctx;
	}
	(void)memset(table_ctx, 0, sizeof(struct ummu_mapt_table_ctx));

	ret = ummu_create_seg_mng(&table_ctx->granted_addr_mng);
	if (ret != 0) {
		UMMU_MAPT_ERROR_LOG("Init granted addr manager failed.\n");
		ret = -ENOMEM;
		goto err_create_rbtree;
	}

	mapt_info->block_base.table_ctx = table_ctx;
	mapt_info->block_base.table_ctx->expan =
		(info->hw_cap & HW_CAP_EXPAN) ? true : false;
	mapt_info->block_base.table_ctx->blk_exp_size = info->blk_exp_size;

	root = ummu_alloc_level_block(mapt_info, &pre_node, block);
	if (root == NULL) {
		UMMU_MAPT_ERROR_LOG("Alloc level block failed.\n");
		ret = -errno;
		goto err_alloc_level_block;
	}

	return 0;
err_alloc_level_block:
	ummu_destroy_seg_mng(&table_ctx->granted_addr_mng);
err_create_rbtree:
	free(table_ctx);
err_malloc_table_ctx:
	table_ctx = NULL;
	return ret;
}

static struct ummu_mapt_info *ummu_alloc_mapt_mem(struct ummu_tid_info *info)
{
	struct ummu_mapt_info *mapt_info;

	mapt_info = (struct ummu_mapt_info *)malloc(sizeof(struct ummu_mapt_info));
	if (mapt_info == NULL) {
		UMMU_MAPT_ERROR_LOG("Alloc mapt info memory failed.\n");
		return NULL;
	}
	(void)memset(mapt_info, 0, sizeof(struct ummu_mapt_info));

	(void)pthread_mutex_init(&mapt_info->mapt_mutex, NULL);
	mapt_info->mode = info->mode;
	mapt_info->tid = info->tid;
	mapt_info->valid = 1;
	mapt_info->double_plbi = info->hw_cap & HW_CAP_DOUBLE_PLBI;
	mapt_info->kcmd_plbi = info->hw_cap & HW_CAP_KCMD_PLBI;
	mapt_info->positive_plbi = info->hw_cap & HW_CAP_PPLBI;
	mapt_info->free_bit = info->hw_cap & HW_CAP_FREE_BIT;

	return mapt_info;
}

static struct ummu_mapt_info *ummu_mapt_create(struct ummu_tid_info *info)
{
	struct ummu_mapt_info *mapt_info;
	int ret;

	mapt_info = ummu_alloc_mapt_mem(info);
	if (mapt_info == NULL) {
		goto err_alloc_mapt_info;
	}

	ret = ummu_queue_create(mapt_info, info, get_ummu_ctx()->shared_fd);
	if (ret != 0) {
		UMMU_MAPT_ERROR_LOG("Alloc plb resource failed.\n");
		goto err_queue_create;
	}

	if (mapt_info->mode == MAPT_MODE_ENTRY) {
		ret = ummu_mapt_entry_init(mapt_info);
	} else {
		ret = ummu_mapt_table_ctx_init(mapt_info, info);
	}
	if (ret != 0) {
		goto err_mapt_init;
	}

	return mapt_info;

err_mapt_init:
	ummu_queue_destroy(mapt_info);
err_queue_create:
	free(mapt_info);
err_alloc_mapt_info:
	return NULL;
}

static int ummu_check_mode(enum ummu_mapt_mode mode)
{
	if (mode >= MAPT_MODE_END || mode < MAPT_MODE_TABLE) {
		UMMU_MAPT_ERROR_LOG("Perm table mode[%u] is invalid.\n", mode);
		return -EINVAL;
	}

	return 0;
}

static int ummu_check_mapt_map(void *mapt_map)
{
	if (!mapt_map) {
		UMMU_MAPT_ERROR_LOG("mapt_map is NULL!\n");
		return -EINVAL;
	}
	return 0;
}

static int ummu_check_ebit(enum ummu_ebit_state e_bit)
{
	if (e_bit >= UMMU_EBIT_END || e_bit < UMMU_EBIT_OFF) {
		UMMU_MAPT_ERROR_LOG("Perm table e_bit[%u] is invalid.\n", e_bit);
		return -EINVAL;
	}

	return 0;
}

static int ummu_check_input_param(enum ummu_mapt_mode mode, unsigned int *tid)
{
	int ret;

	ret = ummu_check_mode(mode);
	if (ret != 0) {
		return ret;
	}

	if (tid == NULL) {
		UMMU_MAPT_ERROR_LOG("Tid is null.\n");
		return -EINVAL;
	}

	return 0;
}

static int ummu_get_token(struct ummu_token_info *token, uint32_t *value)
{
	int ret;

	if (token->input == 0) {
		*value = token->tokenVal;
		return 0;
	} else if (token->input == 1) {
		(void)pthread_mutex_lock(&g_random_mutex);
		ret = ummu_gen_random(value);
		(void)pthread_mutex_unlock(&g_random_mutex);
		if (ret != 0) {
			return -EINVAL;
		}
		token->tokenVal = *value;
		return 0;
	} else {
		UMMU_MAPT_ERROR_LOG("Token input is invalid.\n");
		return -EINVAL;
	}
}

static int ummu_add_token(void *mapt_node, struct ummu_data_info *data_info)
{
	struct ummu_mapt_entry_node *node = (struct ummu_mapt_entry_node *)mapt_node;

	if (node->valid == 1 && node->nonce == 1) {
		node->token_val_1 = data_info->tokenval;
		node->nonce = UMMU_MAX_TOKEN_NUM;

		return 0;
	}

	UMMU_MAPT_ERROR_LOG("Node or nonce is invalid!\n");
	return -EINVAL;
}

static int ummu_remove_token(void *mapt_node, struct ummu_data_info *data_info)
{
	struct ummu_mapt_entry_node *node = (struct ummu_mapt_entry_node *)mapt_node;

	if (node->token_val_0 == data_info->tokenval) {
		node->token_val_0 = node->token_val_1;
	} else if (node->token_val_1 == data_info->tokenval) {
		node->token_val_1 = node->token_val_0;
	} else {
		UMMU_MAPT_ERROR_LOG("Tokenval is invalid!\n");
		return -EINVAL;
	}

	node->nonce = 1;
	return 0;
}

static int ummu_update_token(void *mapt_node, struct ummu_data_info *data_info)
{
	int ret;

	switch (data_info->op) {
		case UMMU_ADD_TOKEN:
			ret = ummu_add_token(mapt_node, data_info);
			break;
		case UMMU_REMOVE_TOKEN:
			ret = ummu_remove_token(mapt_node, data_info);
			break;
		default:
			UMMU_MAPT_ERROR_LOG("Op type is invalid.\n");
			ret = -EINVAL;
			break;
	}
	return ret;
}

static void ummu_entry_fill_node(struct ummu_mapt_entry_node *node, struct ummu_data_info *data_info)
{
	node->permission = (uint32_t)data_info->perm;
	node->base_low = ENTRY_ADDR_LOW(data_info->data_base);
	node->base_high = ENTRY_ADDR_HIGH(data_info->data_base);
	node->limit_low = ENTRY_ADDR_LOW(data_info->data_limit);
	node->limit_high = ENTRY_ADDR_HIGH(data_info->data_limit);
	node->token_check = data_info->token_check;
	node->e_bit = (uint32_t)data_info->e_bit;
	if (data_info->token_check == 1U) {
		node->token_val_0 = data_info->tokenval;
		node->token_val_1 = data_info->tokenval;
		node->nonce = 1;
	}
	node->valid = 1;
	ummu_to_device_wmb();
}

static void ummu_table_fill_node(struct ummu_mapt_table_node *node, uint64_t base, uint64_t limit,
	struct ummu_data_info *data_info, bool new_node)
{
	node->permission = (uint32_t)data_info->perm;
	node->base_low = TABLE_ADDR_LOW(base);
	node->base_high = TABLE_ADDR_HIGH(base);
	node->limit_low = TABLE_ADDR_LOW(limit);
	node->limit_high = TABLE_ADDR_HIGH(limit);
	node->token_check = data_info->token_check;
	node->e_bit = (uint32_t)data_info->e_bit;
	if (data_info->token_check == 1U) {
		node->token_val_0 = data_info->tokenval;
		node->token_val_1 = data_info->tokenval;
		node->nonce = 1;
	}
	node->f_bit = 0;

	if (new_node) {
		node->type = 1;
		node->valid = 1;
	}
	ummu_to_device_wmb();
}

static inline void ummu_modify_entry_cnt(struct ummu_mapt_block *block, uint64_t lv_offset, int cnt)
{
	uint32_t idx = (uint32_t)lv_offset / MAX_MAPT_ENTRY_INDEX;
	if (cnt > 0) {
		block->level_entry_cnt[idx] += (uint16_t)cnt;
		return;
	}
	block->level_entry_cnt[idx] -= (uint16_t)(-cnt);
}

static void ummu_table_clear_node(struct ummu_mapt_table_node *node, struct ummu_mapt_info *mapt_info,
	struct ummu_mapt_table_node *pre_node)
{
	struct ummu_mapt_block *mapt_block = (struct ummu_mapt_block *)
		mapt_info->block_base.table_ctx->mapt_block_array[pre_node->next_lv_index];
	uint32_t lv_offset = TABLE_LVL_OFFSET(pre_node->next_lv_offset_low, pre_node->next_lv_offset_high);

	if (mapt_block == NULL) {
		UMMU_MAPT_ERROR_LOG("Mapt block is invalid.\n");
		return;
	}

	if (node->type == 1) {
		(void)memset(node, 0, sizeof(struct ummu_mapt_table_node));
		ummu_modify_entry_cnt(mapt_block, lv_offset, -1);
		if (mapt_block->level_entry_cnt[lv_offset / INDEX_MAX_SIZE] == 0) {
			ummu_free_level_block(mapt_info, pre_node);
			pre_node->type = 1;
			pre_node->next_block = 0;
			pre_node->next_lv_offset_low = 0;
			pre_node->next_lv_offset_high = 0;
			pre_node->next_lv_index = 0;
		}
		ummu_to_device_wmb();
	} else {
		UMMU_MAPT_ERROR_LOG("Mapt block node type is invalid!\n");
	}
}

static int ummu_table_fill_node_by_level(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, uint64_t lvl_base, uint64_t lvl_limit);
static int ummu_table_fill_head_node_free_bit(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, struct ummu_mapt_table_node *cur_node,
	uint64_t node_base, uint64_t node_limit)
{
	struct ummu_mapt_block * mapt_blk =
	(struct ummu_mapt_block *)data_info->mapt_info->block_base.table_ctx->mapt_block_array[pre_node->next_lv_index];
	struct ummu_mapt_table_node *next_lvl_blk_base;
	uint64_t lvl_offset = TABLE_LVL_OFFSET(pre_node->next_lv_offset_low, pre_node->next_lv_offset_high);

	if (cur_node->valid == 0) {
		ummu_table_fill_node(cur_node, node_base, node_limit, data_info, true);
		ummu_modify_entry_cnt(mapt_blk, lvl_offset, 1);
		return 0;
	}

	if (cur_node->f_bit) {
		ummu_table_fill_node(cur_node, node_base, node_limit, data_info, false);
		ummu_plbi_f_bit_cmd(data_info->mapt_info, cur_node->next_lv_index,
			TABLE_LVL_OFFSET(cur_node->next_lv_offset_low, cur_node->next_lv_offset_high));
		return 0;
	}

	if (cur_node->type == 1) {
		next_lvl_blk_base = ummu_alloc_level_block(data_info->mapt_info, cur_node, mapt_blk);
		if (next_lvl_blk_base == NULL) {
			UMMU_MAPT_ERROR_LOG("Alloc new level_block failed.\n");
			return -errno;
		}
		cur_node->type = 0;
	}

	return ummu_table_fill_node_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
}

static void ummu_table_type_switch_plbi(struct ummu_data_info *data_info, uint64_t node_base,
					uint64_t node_limit, uint32_t level)
{
	uint64_t va, size, full_addr;

	if (data_info->lvl < MAX_LEVEL_INDEX && !data_info->head_flag) {
		full_addr = data_info->data_limit;
	} else {
		full_addr = data_info->data_base;
	}

	va = (full_addr & ~GET_LEVEL_RANGE_MASK(level)) | node_base;
	size = node_limit - node_base + 1;

	ummu_plbi_va_cmd(data_info->mapt_info, va, size);
}

static int ummu_table_fill_head_node(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, struct ummu_mapt_table_node *cur_node,
	uint64_t node_base, uint64_t node_limit)
{
	struct ummu_mapt_block * mapt_blk =
	(struct ummu_mapt_block *)data_info->mapt_info->block_base.table_ctx->mapt_block_array[pre_node->next_lv_index];
	struct ummu_mapt_table_node *next_lvl_blk_base;
	uint64_t lvl_offset = TABLE_LVL_OFFSET(pre_node->next_lv_offset_low, pre_node->next_lv_offset_high);
	uint64_t cur_base, cur_limit;

	cur_base = ADDR_FULL(cur_node->base_low, cur_node->base_high);
	cur_limit = ADDR_FULL(cur_node->limit_low, cur_node->limit_high);
	if (cur_node->valid == 1) {
		if (!IS_RTE_IN_USE(cur_base) && !IS_RTE_IN_USE(cur_limit)) {
			ummu_table_fill_node(cur_node, node_base, node_limit, data_info, false);
			ummu_table_type_switch_plbi(data_info, 0, INVALID_ADDR, level);
			return 0;
		}

		if (cur_node->type == 1) {
			next_lvl_blk_base = ummu_alloc_level_block(data_info->mapt_info, cur_node, mapt_blk);
			if (next_lvl_blk_base == NULL) {
				UMMU_MAPT_ERROR_LOG("Alloc new level_block failed.\n");
				return -errno;
			}
			cur_node->type = 0;
		}
		return ummu_table_fill_node_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
	} else {
		ummu_table_fill_node(cur_node, node_base, node_limit, data_info, true);
		ummu_modify_entry_cnt(mapt_blk, lvl_offset, 1);
	}

	return 0;
}

static int ummu_table_fill_node_by_level(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, uint64_t lvl_base, uint64_t lvl_limit)
{
	struct ummu_mapt_table_node *lvl_blk_base, *cur_node;
	uint64_t node_base, node_limit, lvl_msk, lvl_offset;
	struct ummu_mapt_block *mapt_blk;
	uint16_t base_idx, limit_idx;
	int ret;

	if (level > MAX_LEVEL_INDEX) {
		UMMU_MAPT_ERROR_LOG("Level overflow!\n");
		return -EINVAL;
	}

	lvl_offset = TABLE_LVL_OFFSET(pre_node->next_lv_offset_low, pre_node->next_lv_offset_high);
	mapt_blk = (struct ummu_mapt_block *)
		data_info->mapt_info->block_base.table_ctx->mapt_block_array[pre_node->next_lv_index];
	if (mapt_blk == NULL) {
		UMMU_MAPT_ERROR_LOG("Mapt block is invalid.\n");
		return -EINVAL;
	}
	lvl_blk_base = (struct ummu_mapt_table_node *)mapt_blk->block_addr + lvl_offset;

	base_idx = (uint16_t)GET_LEVEL_BLOCK_INDEX(lvl_base, level);
	limit_idx = (uint16_t)GET_LEVEL_BLOCK_INDEX(lvl_limit, level);
	lvl_msk = GET_LEVEL_RANGE_MASK(level);

	if (data_info->lvl >= MAX_LEVEL_INDEX && base_idx != limit_idx) {
		data_info->lvl = level;
	}

	for (uint16_t i = base_idx; i <= limit_idx; i++) {
		cur_node = lvl_blk_base + i;
		node_base = (i == base_idx) ? (lvl_base & lvl_msk) : 0U;
		node_limit = (i == limit_idx) ? (lvl_limit & lvl_msk) : lvl_msk;

		/* middle rtes */
		if (base_idx < i && i < limit_idx) {
			if (cur_node->valid == 1) {
				UMMU_MAPT_ERROR_LOG("Node suppose to be invalid.\n");
				return -EINVAL;
			}
			ummu_table_fill_node(cur_node, node_base, node_limit, data_info, true);
			ummu_modify_entry_cnt(mapt_blk, lvl_offset, 1);
			continue;
		}

		/* head or tail rte */
		if (data_info->lvl == level) {
			data_info->head_flag = (i == base_idx) ? 1 : 0;
		}

		if (data_info->mapt_info->free_bit) {
			ret = ummu_table_fill_head_node_free_bit(data_info, level, pre_node, cur_node, node_base, node_limit);
		} else {
			ret = ummu_table_fill_head_node(data_info, level, pre_node, cur_node, node_base, node_limit);
		}
		if (ret) {
			return ret;
		}
	}

	return 0;
}

static int ummu_table_clear_node_by_level(struct ummu_data_info *data_info,
	uint32_t level, struct ummu_mapt_table_node *pre_node, uint64_t lvl_base, uint64_t lvl_limit);
static int ummu_table_clear_head_node(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, struct ummu_mapt_table_node *cur_node,
	uint64_t node_base, uint64_t node_limit)
{
	uint64_t cur_base, cur_limit;
	int ret;

	cur_base = ADDR_FULL(cur_node->base_low, cur_node->base_high);
	cur_limit = ADDR_FULL(cur_node->limit_low, cur_node->limit_high);
	if (!IS_RTE_IN_USE(cur_base) && !IS_RTE_IN_USE(cur_limit)) {
		if (cur_node->type == 1) {
			UMMU_MAPT_ERROR_LOG("Next level not exist.\n");
			return -EINVAL;
		}

		ret = ummu_table_clear_node_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
		if (cur_node->type == 1) {
			ummu_table_clear_node(cur_node, data_info->mapt_info, pre_node);
			ummu_table_type_switch_plbi(data_info, 0, INVALID_ADDR, level);
		}

		return ret;
	}

	if (cur_base == node_base && cur_limit == node_limit) {
		if (cur_node->type == 0) {
			cur_node->base_low = INVALID_ADDR;
			cur_node->limit_low = INVALID_ADDR;
			cur_node->base_high = 0;
			cur_node->limit_high = 0;
			cur_node->permission = 0;
			ummu_to_device_wmb();
			return 0;
		}

		ummu_table_clear_node(cur_node, data_info->mapt_info, pre_node);
		return 0;
	}

	if (cur_node->type == 1) {
		UMMU_MAPT_ERROR_LOG("Next level not exist.\n");
		return -EINVAL;
	}

	ret = ummu_table_clear_node_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
	if (cur_node->type == 1) {
		ummu_table_type_switch_plbi(data_info, cur_base, cur_limit, level);
	}

	return ret;
}

static int ummu_table_clear_head_node_free_bit(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, struct ummu_mapt_table_node *cur_node,
	uint64_t node_base, uint64_t node_limit)
{
	uint32_t lvl_idx, lvl_offset;
	uint64_t cur_base, cur_limit;
	int ret;

	lvl_idx = cur_node->next_lv_index;
	lvl_offset = TABLE_LVL_OFFSET(cur_node->next_lv_offset_low, cur_node->next_lv_offset_high);
	if (cur_node->f_bit) {
		if (cur_node->type == 1) {
			UMMU_MAPT_ERROR_LOG("Next level not exist.\n");
			return -EINVAL;
		}

		ret = ummu_table_clear_node_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
		if (cur_node->type == 1) {
			ummu_table_clear_node(cur_node, data_info->mapt_info, pre_node);
			ummu_plbi_f_bit_cmd(data_info->mapt_info, lvl_idx, lvl_offset);
		}

		return ret;
	}

	cur_base = ADDR_FULL(cur_node->base_low, cur_node->base_high);
	cur_limit = ADDR_FULL(cur_node->limit_low, cur_node->limit_high);
	if (cur_base == node_base && cur_limit == node_limit) {
		if (cur_node->type == 0) {
			cur_node->f_bit = 1;
			return 0;
		}

		ummu_table_clear_node(cur_node, data_info->mapt_info, pre_node);
		return 0;
	}

	if (cur_node->type == 1) {
		UMMU_MAPT_ERROR_LOG("Next level not exist.\n");
		return -EINVAL;
	}

	ret =  ummu_table_clear_node_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
	if (cur_node->type == 1) {
		ummu_table_type_switch_plbi(data_info, cur_base, cur_limit, level);
	}

	return ret;
}

static int ummu_table_clear_node_by_level(struct ummu_data_info *data_info,
	uint32_t level, struct ummu_mapt_table_node *pre_node, uint64_t lvl_base, uint64_t lvl_limit)
{
	struct ummu_mapt_table_node *lvl_blk_base, *cur_node;
	uint64_t node_base, node_limit, lvl_msk;
	struct ummu_mapt_block *mapt_blk;
	uint16_t base_idx, limit_idx;
	uint32_t next_lv_offset;
	int ret;

	if (level > MAX_LEVEL_INDEX) {
		UMMU_MAPT_ERROR_LOG("Level overflow.\n");
		return -EINVAL;
	}

	mapt_blk =
	(struct ummu_mapt_block *)data_info->mapt_info->block_base.table_ctx->mapt_block_array[pre_node->next_lv_index];
	if (mapt_blk == NULL) {
		UMMU_MAPT_ERROR_LOG("Mapt block is invalid.\n");
		return -EINVAL;
	}
	next_lv_offset = TABLE_LVL_OFFSET(pre_node->next_lv_offset_low, pre_node->next_lv_offset_high);
	lvl_blk_base = (struct ummu_mapt_table_node *)mapt_blk->block_addr + next_lv_offset;

	base_idx = (uint16_t)GET_LEVEL_BLOCK_INDEX(lvl_base, level);
	limit_idx = (uint16_t)GET_LEVEL_BLOCK_INDEX(lvl_limit, level);
	lvl_msk = GET_LEVEL_RANGE_MASK(level);

	if (data_info->lvl >= MAX_LEVEL_INDEX && base_idx != limit_idx) {
		data_info->lvl = level;
	}

	for (uint16_t i = base_idx; i <= limit_idx; i++) {
		cur_node = lvl_blk_base + i;
		if (cur_node->valid == 0) {
			UMMU_MAPT_ERROR_LOG("Unexpected failed, rte suppose to be valid.\n");
			return -EINVAL;
		}

		node_base = (i == base_idx) ? (lvl_base & lvl_msk) : 0UL;
		node_limit = (i == limit_idx) ? (lvl_limit & lvl_msk) : lvl_msk;

		/* middle rtes */
		if (base_idx < i && i < limit_idx) {
			ummu_table_clear_node(cur_node, data_info->mapt_info, pre_node);
			continue;
		}

		/* head or tail rte */
		if (data_info->lvl == level) {
			data_info->head_flag = (i == base_idx) ? 1 : 0;
		}

		if (data_info->mapt_info->free_bit) {
			ret = ummu_table_clear_head_node_free_bit(data_info, level, pre_node, cur_node, node_base, node_limit);
		} else {
			ret = ummu_table_clear_head_node(data_info, level, pre_node, cur_node, node_base, node_limit);
		}
		if (ret != 0) {
			return ret;
		}
	}

	return 0;
}

static int ummu_table_update_token_by_level(struct ummu_data_info *data_info, uint32_t level,
	struct ummu_mapt_table_node *pre_node, uint64_t lvl_base, uint64_t lvl_limit);
static int ummu_table_update_node_token_free_bit(struct ummu_data_info *data_info,
	uint32_t level, struct ummu_mapt_table_node *cur_node,
	uint64_t node_base, uint64_t node_limit)
{
	uint64_t cur_base, cur_limit;

	if (cur_node->f_bit) {
		if (cur_node->type == 1) {
			UMMU_MAPT_ERROR_LOG("Next level not exist.\n");
			return -EINVAL;
		}

		return ummu_table_update_token_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
	}

	cur_base = ADDR_FULL(cur_node->base_low, cur_node->base_high);
	cur_limit = ADDR_FULL(cur_node->limit_low, cur_node->limit_high);
	if (cur_base == node_base && cur_limit == node_limit) {
		return ummu_update_token((void *)cur_node, data_info);
	}

	if (cur_node->type == 1) {
		UMMU_MAPT_ERROR_LOG("Next level not exist.\n");
		return -EINVAL;
	}

	return ummu_table_update_token_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
}

static int ummu_table_update_node_token(struct ummu_data_info *data_info,
	uint32_t level, struct ummu_mapt_table_node *cur_node,
	uint64_t node_base, uint64_t node_limit)
{
	uint64_t cur_base, cur_limit;
	int ret;

	cur_base = ADDR_FULL(cur_node->base_low, cur_node->base_high);
	cur_limit = ADDR_FULL(cur_node->limit_low, cur_node->limit_high);
	if (cur_base == node_base && cur_limit == node_limit) {
		ret = ummu_update_token((void *)cur_node, data_info);
		if (ret != 0) {
			return ret;
		}
	} else if (cur_node->type == 0) {
		return ummu_table_update_token_by_level(data_info, level + 1U, cur_node, node_base, node_limit);
	} else {
		UMMU_MAPT_ERROR_LOG("Unexpected failed.\n");
		return -EINVAL;
	}

	return 0;
}

static int ummu_table_update_token_by_level(struct ummu_data_info *data_info,
	uint32_t level, struct ummu_mapt_table_node *pre_node,
	uint64_t lvl_base, uint64_t lvl_limit)
{
	uint64_t node_base, node_limit, lvl_msk;
	struct ummu_mapt_table_node *lvl_blk_base, *cur_node;
	struct ummu_mapt_block *mapt_blk;
	uint16_t base_idx, limit_idx;
	int ret;

	if (level > MAX_LEVEL_INDEX) {
		UMMU_MAPT_ERROR_LOG("Level overflow.\n");
		return -EINVAL;
	}

	mapt_blk = (struct ummu_mapt_block *)
		data_info->mapt_info->block_base.table_ctx->mapt_block_array[pre_node->next_lv_index];
	if (mapt_blk == NULL) {
		UMMU_MAPT_ERROR_LOG("Mapt block is invalid.\n");
		return -EINVAL;
	}
	lvl_blk_base = (struct ummu_mapt_table_node *)mapt_blk->block_addr +
		TABLE_LVL_OFFSET(pre_node->next_lv_offset_low, pre_node->next_lv_offset_high);

	base_idx = (uint16_t)GET_LEVEL_BLOCK_INDEX(lvl_base, level);
	limit_idx = (uint16_t)GET_LEVEL_BLOCK_INDEX(lvl_limit, level);
	lvl_msk = GET_LEVEL_RANGE_MASK(level);

	for (uint16_t i = base_idx; i <= limit_idx; i++) {
		cur_node = lvl_blk_base + i;
		if (cur_node->valid == 0) {
			UMMU_MAPT_ERROR_LOG("Unexpected failed, rte suppose to be valid.\n");
			return -EINVAL;
		}

		node_base = (i == base_idx) ? (lvl_base & lvl_msk) : 0UL;
		node_limit = (i == limit_idx) ? (lvl_limit & lvl_msk) : lvl_msk;

		/* middle rtes */
		if (base_idx < i && i < limit_idx) {
			ret = ummu_update_token((void *)cur_node, data_info);
			if (ret != 0) {
				return ret;
			}
			continue;
		}

		/* head or tail rte */
		if (data_info->mapt_info->free_bit) {
			ret = ummu_table_update_node_token_free_bit(data_info, level, cur_node, node_base, node_limit);
		} else {
			ret = ummu_table_update_node_token(data_info, level, cur_node, node_base, node_limit);
		}
		if (ret) {
			return ret;
		}
	}

	return 0;
}

static int ummu_check_perm(enum ummu_mapt_perm perm)
{
	int ret = 0;

	switch (perm) {
		case MAPT_PERM_R:
			break;
		case MAPT_PERM_W:
			break;
		case MAPT_PERM_RW:
			break;
		case MAPT_PERM_ATOMIC_R:
			break;
		case MAPT_PERM_ATOMIC_W:
			break;
		case MAPT_PERM_ATOMIC_RW:
			break;
		default:
			UMMU_MAPT_ERROR_LOG("Permission value is invalid.\n");
			ret = -EINVAL;
			break;
	}
	return ret;
}

static int ummu_check_data(struct ummu_data_info *data_info, enum ummu_mapt_mode mode)
{
	if (data_info->data_size == 0) {
		UMMU_MAPT_ERROR_LOG("Argument data_size is invalid.\n");
		return -EINVAL;
	}

	if ((uint64_t)data_info->data >> MAX_ADDRESS_BITS != 0 ||
		((uint64_t)data_info->data_size - (uint64_t)1) >> MAX_ADDRESS_BITS != 0 ||
		((uint64_t)data_info->data + (uint64_t)data_info->data_size - 1UL) >> MAX_ADDRESS_BITS != 0) {
		UMMU_MAPT_ERROR_LOG("The address or memory size exceeds the upper limit.\n");
		return -EINVAL;
	}

	if (mode == MAPT_MODE_TABLE && !ALIGN_VPAGE_SHIFT_CHECK((uint64_t)data_info->data)) {
		UMMU_MAPT_ERROR_LOG("Table mode, data_base must be 4K aligned.\n");
		return -EINVAL;
	}

	if (ummu_check_perm(data_info->perm) != 0 ||
		ummu_check_ebit(data_info->e_bit) != 0) {
		return -EINVAL;
	}

	return 0;
}

static int ummu_perm_data_preproc(struct ummu_data_info *data_info)
{
	int ret;

	data_info->token_check = (data_info->token != NULL) ? 1U : 0U;
	if (data_info->token_check == 1UL) {
		ret = ummu_get_token(data_info->token, &data_info->tokenval);
		if (ret != 0) {
			return ret;
		}
	}

	data_info->data_base = (uint64_t)(uintptr_t)data_info->data;
	data_info->data_limit = (uint64_t)(uintptr_t)data_info->data + (uint64_t)data_info->data_size - 1UL;

	return 0;
}

static int ummu_entry_op(struct ummu_mapt_info *mapt_info, struct ummu_data_info *data_info)
{
	struct ummu_mapt_entry_node *node = (struct ummu_mapt_entry_node *)mapt_info->block_base.entry_block;
	int ret = 0;

	switch (data_info->op) {
		case UMMU_GRANT:
			ummu_entry_fill_node(node, data_info);
			break;
		case UMMU_ADD_TOKEN:
		case UMMU_REMOVE_TOKEN:
			ret = ummu_update_token((void *)node, data_info);
			break;
		case UMMU_UNGRANT:
			(void)memset(node, 0, sizeof(struct ummu_mapt_entry_node));
			break;
		default:
			UMMU_MAPT_ERROR_LOG("Op code is invalid.\n");
			ret = -EINVAL;
			break;
	}
	return ret;
}

static int ummu_table_op(struct ummu_mapt_info *mapt_info, struct ummu_data_info *data_info)
{
	struct ummu_mapt_table_node node = {0};
	data_info->mapt_info = mapt_info;
	int ret;

	switch (data_info->op) {
		case UMMU_GRANT:
			ret = ummu_table_fill_node_by_level(data_info, 0, &node,
							    data_info->data_base, data_info->data_limit);
			if (ret != 0) {
				(void)ummu_table_clear_node_by_level(data_info, 0, &node,
								     data_info->data_base, data_info->data_limit);
			}
			break;
		case UMMU_ADD_TOKEN:
		case UMMU_REMOVE_TOKEN:
			ret = ummu_table_update_token_by_level(data_info, 0, &node,
								data_info->data_base, data_info->data_limit);
			break;
		case UMMU_UNGRANT:
			ret = ummu_table_clear_node_by_level(data_info, 0, &node,
							     data_info->data_base, data_info->data_limit);
			break;
		default:
			UMMU_MAPT_ERROR_LOG("Op code is invalid.\n");
			ret = -EINVAL;
			break;
	}

	return ret;
}

static int ummu_grant_imp(struct ummu_mapt_info *mapt_info, struct ummu_data_info *data)
{
	int ret;

	if (mapt_info->mode == MAPT_MODE_ENTRY) {
		ret = ummu_entry_op(mapt_info, data);
	} else if (mapt_info->mode == MAPT_MODE_TABLE) {
		ret = ummu_table_op(mapt_info, data);
	} else {
		UMMU_MAPT_ERROR_LOG("Mode is invalid, only support entry or table mode.\n");
		return -EINVAL;
	}

	if (mapt_info->positive_plbi) {
		return ret;
	}

	if (data->op == UMMU_ADD_TOKEN || ret != 0) {
		ummu_plbi_va_cmd(mapt_info, data->data_base, data->data_size);
	}

	return ret;
}

static struct ummu_mapt_info *get_mapt_info_by_tid(struct ummu_ctx_info *ummu_ctx, uint32_t tid)
{
	struct ummu_mapt_info *mapt_info;

	mapt_info = (struct ummu_mapt_info *)ummu_map_get(ummu_ctx->mapt_map, tid);
	if (mapt_info == NULL || mapt_info->valid == 0 || ummu_check_mode(mapt_info->mode) != 0) {
		UMMU_MAPT_ERROR_LOG("Mapt info %s null.\n", mapt_info == NULL ? "is" : "is not");
		return NULL;
	}

	return mapt_info;
}

static int ummu_update_info(enum ummu_grant_op_type opt, struct ummu_mapt_info *mapt_info,
									   struct ummu_data_info *grant_param)
{
	int ret = 0;

	if (mapt_info->mode != MAPT_MODE_TABLE) {
		return ret;
	}

	switch (opt) {
		case UMMU_GRANT:
			ret = ummu_insert_new_addr(mapt_info->block_base.table_ctx->granted_addr_mng, grant_param);
			break;
		case UMMU_ADD_TOKEN:
		case UMMU_REMOVE_TOKEN:
			ret = ummu_token_update(mapt_info->block_base.table_ctx->granted_addr_mng, grant_param, opt);
			break;
		case UMMU_UNGRANT:
			ret = ummu_delete_addr(mapt_info->block_base.table_ctx->granted_addr_mng, grant_param);
			break;
		default:
			ret = -EINVAL;
			break;
	}
	return ret;
}

int ummu_grant(uint32_t tid, void *data, size_t data_size, enum ummu_mapt_perm perm, struct ummu_seg_attr *seg_attr)
{
	struct ummu_ctx_info *ummu_ctx = get_ummu_ctx();
	struct ummu_mapt_info *mapt_info;
	struct ummu_data_info data_info = {0};
	enum ummu_grant_op_type opt;
	int ret;

	if (seg_attr == NULL) {
		UMMU_MAPT_ERROR_LOG("seg_attr is NULL.\n");
		return -EINVAL;
	}

	(void)pthread_mutex_lock(&ummu_ctx->ctx_mutex);
	ret = ummu_check_mapt_map(ummu_ctx->mapt_map);
	if (ret != 0) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return ret;
	}
	mapt_info = get_mapt_info_by_tid(ummu_ctx, tid);
	if (mapt_info == NULL) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return -EINVAL;
	}
	(void)pthread_mutex_lock(&mapt_info->mapt_mutex);

	data_info.data = data;
	data_info.data_size = data_size;
	data_info.perm = perm;
	data_info.token = seg_attr->token;
	data_info.e_bit = seg_attr->e_bit;
	data_info.lvl = MAX_LEVEL_INDEX;

	if ((bool)(ummu_check_data(&data_info, mapt_info->mode) != 0) ||
		(bool)(ummu_perm_data_preproc(&data_info) != 0)) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);
		return -EINVAL;
	}

	opt = ummu_grant_check(mapt_info, &data_info);
	if (opt == UMMU_OP_END) {
		UMMU_MAPT_ERROR_LOG("Check grant failed.\n");
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);
		return -EINVAL;
	}
	data_info.op = opt;
	ret = ummu_grant_imp(mapt_info, &data_info);
	if (ret == 0) {
		ret = ummu_update_info(opt, mapt_info, &data_info);
	}
	(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
	(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);
	data_info.tokenval = 0;
	return ret;
}

static int ummu_ungrant_imp(struct ummu_mapt_info *mapt_info, struct ummu_data_info *data)
{
	int ret;

	if (mapt_info->mode == MAPT_MODE_ENTRY) {
		ret = ummu_entry_op(mapt_info, data);
	} else if (mapt_info->mode == MAPT_MODE_TABLE) {
		ret = ummu_table_op(mapt_info, data);
	} else {
		UMMU_MAPT_ERROR_LOG("Mode is invalid, only support table or entry.\n");
		ret = -EINVAL;
	}

	return ret;
}

static int ummu_ungrant_data(uint32_t tid, void *data, size_t size, uint32_t tokenval, int bytoken)
{
	struct ummu_ctx_info *ummu_ctx = get_ummu_ctx();
	struct ummu_mapt_info *mapt_info;
	struct ummu_data_info data_info = {0};
	enum ummu_grant_op_type opt;
	int ret;

	if (size == 0) {
		UMMU_MAPT_ERROR_LOG("Ummu ungrant size is zero.\n");
		return -ENOMEM;
	}

	(void)pthread_mutex_lock(&ummu_ctx->ctx_mutex);
	ret = ummu_check_mapt_map(ummu_ctx->mapt_map);
	if (ret != 0) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return ret;
	}

	mapt_info = get_mapt_info_by_tid(ummu_ctx, tid);
	if (mapt_info == NULL) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return -EINVAL;
	}
	(void)pthread_mutex_lock(&mapt_info->mapt_mutex);

	data_info.lvl = MAX_LEVEL_INDEX;
	data_info.data = data;
	data_info.data_size = size;
	data_info.tokenval = tokenval;
	data_info.bytoken = bytoken;
	data_info.data_base = (uint64_t)data;
	data_info.data_limit = (uint64_t)data + (uint64_t)size - 1;

	if (mapt_info->mode == MAPT_MODE_TABLE && !ALIGN_VPAGE_SHIFT_CHECK((uint64_t)data_info.data)) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);
		UMMU_MAPT_ERROR_LOG("Table mode, data_base must be 4K aligned.\n");
		ret = -EINVAL;
		goto out;
	}

	opt = ummu_ungrant_check(mapt_info, &data_info);
	if (opt == UMMU_OP_END) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);
		ret = -EINVAL;
		goto out;
	}
	data_info.op = opt;

	ret = ummu_ungrant_imp(mapt_info, &data_info);
	if (ret == 0) {
		ret = ummu_update_info(opt, mapt_info, &data_info);
	}

	ummu_plbi_va_cmd(mapt_info, data_info.data_base, data_info.data_size);
	(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
	(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);

out:
	data_info.tokenval = 0;
	return ret;
}

int ummu_ungrant(uint32_t tid, void *data, size_t size)
{
	return ummu_ungrant_data(tid, data, size, 0, 0);
}

int ummu_ungrant_by_token(uint32_t tid, void *data, size_t size, uint32_t token_val)
{
	return ummu_ungrant_data(tid, data, size, token_val, 1);
}

static void ummu_mapt_table_ctx_uninit(struct ummu_mapt_info *mapt_info)
{
	struct ummu_mapt_table_ctx *table_ctx;
	struct ummu_mapt_block *block;
	unsigned long idx, block_id;

	table_ctx = mapt_info->block_base.table_ctx;

	if (table_ctx->granted_addr_mng != 0) {
		ummu_destroy_seg_mng(&table_ctx->granted_addr_mng);
	}

	FOR_EACH_SET_BIT(idx, table_ctx->level_block_bitmap, MAX_LEVEL_ID_SIZE) {
		block_id = idx / PER_MAPT_LEVEL_BLOCK_CNT;
		if ((idx % PER_MAPT_LEVEL_BLOCK_CNT) != 0) {
			continue;
		}
		block = (struct ummu_mapt_block *)table_ctx->mapt_block_array[block_id];
		table_ctx->mapt_block_array[block_id] = NULL;
		if (block == NULL) {
			UMMU_MAPT_ERROR_LOG("Free block id = %lu failed.\n", block_id);
			continue;
		}
		ummu_free_core_buf(BASE_MODE_TABLE_BLOCK, (void *)block->block_addr, table_ctx->blk_exp_size);
		free(block);
		block = NULL;
	}

	if (mapt_info->block_base.table_ctx != NULL) {
		free(mapt_info->block_base.table_ctx);
		mapt_info->block_base.table_ctx = NULL;
	}
}

static void ummu_mapt_entry_uninit(struct ummu_mapt_info *mapt_info)
{
	ummu_free_core_buf(BASE_MODE_ENTRY_BLOCK, (void *)mapt_info->block_base.entry_block, BLOCK_SIZE_4K);
}

void ummu_mapt_destroy(struct ummu_mapt_info *info)
{
	if (info->mode == MAPT_MODE_ENTRY) {
		ummu_mapt_entry_uninit(info);
	} else if (info->mode == MAPT_MODE_TABLE) {
		ummu_mapt_table_ctx_uninit(info);
	} else {
		UMMU_MAPT_ERROR_LOG("Mode is invalid, only support entry or table.\n");
		return;
	}

	ummu_queue_destroy(info);
	(void)pthread_mutex_destroy(&info->mapt_mutex);
	free(info);
}

int ummu_free_tid(uint32_t tid)
{
	struct ummu_ctx_info *ummu_ctx = get_ummu_ctx();
	struct ummu_mapt_info *mapt_info;
	int ret;

	(void)pthread_mutex_lock(&ummu_ctx->ctx_mutex);
	ret = ummu_check_mapt_map(ummu_ctx->mapt_map);
	if (ret != 0) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return ret;
	}
	mapt_info = (struct ummu_mapt_info *)ummu_map_get(ummu_ctx->mapt_map, tid);
	if (mapt_info == NULL || mapt_info->valid == 0) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return -EINVAL;
	}

	(void)pthread_mutex_lock(&mapt_info->mapt_mutex);
	(void)ummu_map_del(ummu_ctx->mapt_map, tid);
	(void)pthread_mutex_unlock(&mapt_info->mapt_mutex);
	ummu_mapt_destroy(mapt_info);
	ummu_put_tid(ummu_ctx->shared_fd, tid);
	(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
	UMMU_MAPT_INFO_LOG("free tid = %u\n", tid);

	return 0;
}

int ummu_allocate_tid(struct ummu_tid_attr *tid_attr, uint32_t *tid)
{
	struct ummu_tid_info info = {};
	struct ummu_ctx_info *ummu_ctx = get_ummu_ctx();
	struct ummu_mapt_info *mapt_info;
	int ret;

	if (tid_attr == NULL) {
		UMMU_MAPT_ERROR_LOG("tid_attr is NULL.\n");
		return -EINVAL;
	}

	ret = ummu_check_input_param(tid_attr->mode, tid);
	if (ret != 0) {
		return ret;
	}
	info.mode = tid_attr->mode;

	(void)pthread_mutex_lock(&ummu_ctx->ctx_mutex);
	ret = ummu_check_mapt_map(ummu_ctx->mapt_map);
	if (ret != 0) {
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return ret;
	}
	ret = ummu_get_tid(ummu_ctx->shared_fd, &info);
	if (ret != 0) {
		UMMU_MAPT_ERROR_LOG("Get tid failed, ret = %d.\n", ret);
		(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
		return ret;
	}

	mapt_info = ummu_mapt_create(&info);
	if (mapt_info == NULL) {
		UMMU_MAPT_ERROR_LOG("Create ummu mapt failed.\n");
		ret = -ENOMEM;
		goto err_mapt_create;
	}

	ret = ummu_map_insert(ummu_ctx->mapt_map, info.tid, (void *)mapt_info);
	if (ret != 0) {
		UMMU_MAPT_ERROR_LOG("Insert mapt map failed.\n");
		goto err_map_insert;
	}
	(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
	*tid = info.tid;
	UMMU_MAPT_INFO_LOG("allocate tid = %u, mapt_mode = %u\n", info.tid, info.mode);

	return 0;

err_map_insert:
	ummu_mapt_destroy(mapt_info);
err_mapt_create:
	ummu_put_tid(ummu_ctx->shared_fd, info.tid);
	(void)pthread_mutex_unlock(&ummu_ctx->ctx_mutex);
	return ret;
}

