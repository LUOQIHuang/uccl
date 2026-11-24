/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_MAP_H_
#define _UMMU_MAP_H_
#include <stdint.h>

typedef void (*ummu_value_destroy)(void *);

void *ummu_map_create(void);
int ummu_map_insert(void *map, uint32_t key, void *value);
int ummu_map_del(void *map, uint32_t key);
void *ummu_map_get(void *map, uint32_t key);
void ummu_map_clear(void *map, ummu_value_destroy destroyer);

#endif

