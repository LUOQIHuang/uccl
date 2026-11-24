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
#include "ummu_mapt.h"
#include "ummu_seg_tree.h"
#include "ummu_seg_mng.h"

static enum ummu_grant_op_type ummu_table_param_check(struct ummu_seg_info *exist_addr,
	struct ummu_data_info *grant_param)
{
	if ((exist_addr->token_check == 1 && grant_param->token_check == 1) &&
		(exist_addr->permission == grant_param->perm) &&
		(exist_addr->e_bit == grant_param->e_bit) &&
		(exist_addr->token_count == 1)) {
		return UMMU_ADD_TOKEN;
	}

	UMMU_MAPT_ERROR_LOG("Check grant info failed. "
		"Granted addr info(token_check=%u, token_count=%u, permission=%u, e_bit=%u),"
		"target addr info(token_check=%u, permission=%u, e_bit=%u).\n",
		exist_addr->token_check, exist_addr->token_count, exist_addr->permission, exist_addr->e_bit,
		grant_param->token_check, grant_param->perm, grant_param->e_bit);
	return UMMU_OP_END;
}

static enum ummu_grant_op_type ummu_ungrant_param_check(struct ummu_seg_info *exist_addr,
	struct ummu_data_info *grant_param)
{
	if (grant_param->bytoken == 0) {
		return UMMU_UNGRANT;
	}

	if (exist_addr->token_check == 0) {
		UMMU_MAPT_ERROR_LOG("Target segment grant without token, cannot ungrant by token.\n");
		return UMMU_OP_END;
	}

	if (exist_addr->token_val[0] == grant_param->tokenval || exist_addr->token_val[1] == grant_param->tokenval) {
		if (exist_addr->token_count == 1) {
			return UMMU_UNGRANT;
		} else if (exist_addr->token_count == UMMU_MAX_TOKEN_NUM) {
			return UMMU_REMOVE_TOKEN;
		} else {
			UMMU_MAPT_ERROR_LOG("Token count invalid.\n");
		}
	}

	UMMU_MAPT_ERROR_LOG("Token not match.\n");
	return UMMU_OP_END;
}

static void ummu_remove_tokenval(uint32_t tokenval, struct ummu_seg_info *data)
{
	if (data->token_val[0] == tokenval) {
		data->token_val[0] = data->token_val[1];
	} else if (data->token_val[1] == tokenval) {
		data->token_val[1] = data->token_val[0];
	}

	data->token_count = 1;
}

static enum ummu_grant_op_type ummu_entry_param_check(struct ummu_mapt_info *mapt_info,
	struct ummu_data_info *grant_param)
{
	struct ummu_mapt_entry_node *node = mapt_info->block_base.entry_block;
	uint64_t base, limit;

	if (mapt_info->block_base.entry_block->valid == 0) {
		return UMMU_GRANT;
	}

	base = ADDR_FULL(node->base_low, node->base_high);
	limit = ADDR_FULL(node->limit_low, node->limit_high);
	if ((base == grant_param->data_base) &&
		(limit == grant_param->data_limit) &&
		(node->token_check == 1 && grant_param->token_check == 1) &&
		(node->permission == (uint64_t)grant_param->perm) &&
		((int)node->e_bit == (int)grant_param->e_bit) &&
		(node->nonce == 1)) {
		return UMMU_ADD_TOKEN;
	}

	UMMU_MAPT_ERROR_LOG("Node data check failed!\n");
	UMMU_MAPT_DEBUG_LOG("node.perm = %lu, "
		"node.e_bit = %d, node.token_check = %d, node.nonce = %u, perm = %u, "
		"e_bit = %d, token_check = %d.\n", (uint64_t)node->permission,
		(int)node->e_bit, (int)node->token_check, (uint32_t)node->nonce,
		grant_param->perm, (int)grant_param->e_bit, (int)grant_param->token_check);

	return UMMU_OP_END;
}

enum ummu_grant_op_type ummu_grant_check(struct ummu_mapt_info *mapt_info, struct ummu_data_info *grant_param)
{
	struct ummu_seg_info *data;
	int comp_ret;

	if (mapt_info->mode == MAPT_MODE_ENTRY) {
		return ummu_entry_param_check(mapt_info, grant_param);
	}

	if (mapt_info->block_base.table_ctx->granted_addr_mng == 0UL) {
		UMMU_MAPT_ERROR_LOG("Granted addr manager invalid.\n");
		return UMMU_OP_END;
	}

	data = (struct ummu_seg_info *)ummu_find_seg(grant_param->data_base, grant_param->data_size - 1,
		mapt_info->block_base.table_ctx->granted_addr_mng, &comp_ret);

	switch (comp_ret) {
		case RANGE_MATCH:
			return ummu_table_param_check(data, grant_param);
		case RANGE_OVERLAP: {
			UMMU_MAPT_ERROR_LOG("Address range overlap.\n");
			return UMMU_OP_END;
		}
		case RANGE_NOT_EXIST:
			return UMMU_GRANT;
		default: {
			UMMU_MAPT_ERROR_LOG("Fail to check grant addr state.\n");
			return UMMU_OP_END;
		}
	}
}

static enum ummu_grant_op_type ummu_entry_ungrant_param_check(struct ummu_mapt_info *mapt_info,
	struct ummu_data_info *grant_param)
{
	struct ummu_mapt_entry_node *node = mapt_info->block_base.entry_block;
	uint64_t base, limit;

	if (mapt_info->block_base.entry_block->valid == 0) {
		UMMU_MAPT_ERROR_LOG("Entry block is not used.\n");
		return UMMU_OP_END;
	}

	base = ADDR_FULL(node->base_low, node->base_high);
	limit = ADDR_FULL(node->limit_low, node->limit_high);
	if ((base != grant_param->data_base) || (limit != grant_param->data_limit)) {
		UMMU_MAPT_ERROR_LOG("Base %s equal or limit %s equal.\n", base != grant_param->data_base ? "is" : "not",
			limit != grant_param->data_limit ? "is" : "not");
		return UMMU_OP_END;
	}

	if (grant_param->bytoken == 0) {
		return UMMU_UNGRANT;
	}

	if (node->token_check == 1) {
		if ((node->token_val_0 != grant_param->tokenval) && (node->token_val_1 != grant_param->tokenval)) {
			UMMU_MAPT_ERROR_LOG("Token not match.\n");
			return UMMU_OP_END;
		}

		if (node->nonce == 1) {
			return UMMU_UNGRANT;
		} else if (node->nonce == UMMU_MAX_TOKEN_NUM) {
			return UMMU_REMOVE_TOKEN;
		}
	}

	UMMU_MAPT_ERROR_LOG("Check ungrant param failed. Input bytoken=%d, exist token_check=%d.\n",
		grant_param->bytoken, node->token_check);
	return UMMU_OP_END;
}

enum ummu_grant_op_type ummu_ungrant_check(struct ummu_mapt_info *mapt_info, struct ummu_data_info *grant_param)
{
	struct ummu_seg_info *data;
	int comp_ret;

	if (mapt_info->mode == MAPT_MODE_ENTRY) {
		return ummu_entry_ungrant_param_check(mapt_info, grant_param);
	}

	if (mapt_info->block_base.table_ctx->granted_addr_mng == 0UL) {
		UMMU_MAPT_ERROR_LOG("Granted addr manager invalid.\n");
		return UMMU_OP_END;
	}

	data = (struct ummu_seg_info *)ummu_find_seg(grant_param->data_base, grant_param->data_size - 1,
		mapt_info->block_base.table_ctx->granted_addr_mng, &comp_ret);
	if ((data == NULL) || (comp_ret != RANGE_MATCH)) {
		UMMU_MAPT_ERROR_LOG("Find target address range failed.\n");
		return UMMU_OP_END;
	}

	return ummu_ungrant_param_check(data, grant_param);
}

int ummu_create_seg_mng(uint64_t *output)
{
	return ummu_create_seg_tree(output);
}

static void clear_seg_info(void *data)
{
    struct ummu_seg_info *grant_info = (struct ummu_seg_info *)data;
    grant_info->token_val[0] = 0;
    grant_info->token_val[1] = 0;
}

void ummu_destroy_seg_mng(uint64_t *seg_table)
{
	ummu_destroy_seg_tree(seg_table, clear_seg_info);
}

int ummu_insert_new_addr(uint64_t seg_table, struct ummu_data_info *grant_param)
{
	struct ummu_seg_info *grant_info;
	int ret;

	grant_info = (struct ummu_seg_info *)calloc(1, sizeof(struct ummu_seg_info));
	if (grant_info == NULL) {
		UMMU_MAPT_ERROR_LOG("Allocate memory failed.\n");
		return -ENOMEM;
	}

	grant_info->grant_size = grant_param->data_size;
	grant_info->start_addr = grant_param->data_base;
	grant_info->e_bit = grant_param->e_bit;
	grant_info->permission = grant_param->perm;
	grant_info->token_check = grant_param->token_check;
	if (grant_info->token_check) {
		grant_info->token_count = 1;
		grant_info->token_val[0] = grant_param->tokenval;
		grant_info->token_val[1] = grant_param->tokenval;
	}

	ret = ummu_insert_seg((void *)grant_info, seg_table, grant_info->start_addr, grant_info->grant_size - 1);
	if (ret != 0) {
		grant_info->token_val[0] = 0;
		grant_info->token_val[1] = 0;
		free(grant_info);
	}
	return ret;
}

int ummu_delete_addr(uint64_t seg_table, struct ummu_data_info *grant_param)
{
	return ummu_delete_seg(grant_param->data_base, grant_param->data_size - 1, seg_table, clear_seg_info);
}

int ummu_token_update(uint64_t seg_table, struct ummu_data_info *grant_param, enum ummu_grant_op_type op)
{
	struct ummu_seg_info *data;
	int find_ret;

	data = (struct ummu_seg_info *)ummu_find_seg(grant_param->data_base,
		grant_param->data_size - 1, seg_table, &find_ret);
	if (data == NULL) {
		UMMU_MAPT_ERROR_LOG("Find grant address info failed.\n");
		return -EINVAL;
	}

	if (op == UMMU_REMOVE_TOKEN) {
		ummu_remove_tokenval(grant_param->tokenval, data);
		return 0;
	}

	data->token_val[1] = grant_param->tokenval;
	data->token_count = UMMU_MAX_TOKEN_NUM;
	return 0;
}

