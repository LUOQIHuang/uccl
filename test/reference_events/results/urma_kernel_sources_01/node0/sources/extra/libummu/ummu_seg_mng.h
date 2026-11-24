/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_SEG_MNG_H_
#define _UMMU_SEG_MNG_H_

#include <stdint.h>
#include "ummu_mapt.h"
#include "ummu_common.h"

enum ummu_grant_op_type ummu_grant_check(struct ummu_mapt_info *mapt_info, struct ummu_data_info *grant_param);
enum ummu_grant_op_type ummu_ungrant_check(struct ummu_mapt_info *mapt_info, struct ummu_data_info *grant_param);
int ummu_create_seg_mng(uint64_t *output);
void ummu_destroy_seg_mng(uint64_t *seg_table);
int ummu_insert_new_addr(uint64_t seg_table, struct ummu_data_info *grant_param);
int ummu_delete_addr(uint64_t seg_table, struct ummu_data_info *grant_param);
int ummu_token_update(uint64_t seg_table, struct ummu_data_info *grant_param, enum ummu_grant_op_type op);

#endif

