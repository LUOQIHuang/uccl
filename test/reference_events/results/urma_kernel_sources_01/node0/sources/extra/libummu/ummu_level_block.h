/* SPDX-License-Identifier: MIT */
/*
* Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
* THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
* EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
* MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
*/

#ifndef _UMMU_LEVEL_BLOCK_H_
#define _UMMU_LEVEL_BLOCK_H_

#include "ummu_mapt.h"

struct ummu_mapt_table_node *ummu_alloc_level_block(struct ummu_mapt_info *mapt_info,
						    struct ummu_mapt_table_node *pre_node,
						    struct ummu_mapt_block *pre_node_mapt_block);
void ummu_free_level_block(struct ummu_mapt_info *mapt_info, struct ummu_mapt_table_node *prev_node);
#endif

