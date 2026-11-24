/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_SEG_TREE_H_
#define _UMMU_SEG_TREE_H_

#include <sys/types.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum range_compare_ret {
	RANGE_MATCH = 0,
	RANGE_OVERLAP,
	RANGE_NOT_EXIST
};
typedef void (*clear_seg_node)(void *);

int ummu_create_seg_tree(uint64_t *output);
int ummu_insert_seg(void *data, uint64_t seg_tree, uint64_t left, uint64_t size);
void *ummu_find_seg(uint64_t left, uint64_t size, uint64_t seg_tree, int *comp);
int ummu_delete_seg(uint64_t left, uint64_t size, uint64_t seg_tree, clear_seg_node cleaner);
void ummu_destroy_seg_tree(uint64_t *seg_tree, clear_seg_node cleaner);
#ifdef __cplusplus
}
#endif

#endif

