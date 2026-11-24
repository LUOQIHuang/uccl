/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_UTILS_H_
#define _UMMU_UTILS_H_
#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif
typedef void (*value_destroy)(void *);

void *create_map(void);
int hashmap_insert(void *map, uint32_t key, void *value);
size_t hashmap_del(void *map, uint32_t key);
void *hashmap_get(void *map, uint32_t key);
void hashmap_clear(void *map, value_destroy destroyer);

#ifdef __cplusplus
}
#endif
#endif

