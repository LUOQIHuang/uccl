// SPDX-License-Identifier: MIT
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#include "ummu_map.h"
#include "ummu_utils.h"

void *ummu_map_create(void)
{
	return create_map();
}

int ummu_map_insert(void *map, uint32_t key, void *value)
{
	return hashmap_insert(map, key, value);
}

void *ummu_map_get(void *map, uint32_t key)
{
	return hashmap_get(map, key);
}

int ummu_map_del(void *map, uint32_t key)
{
	return hashmap_del(map, key);
}

void ummu_map_clear(void *map, ummu_value_destroy destroyer)
{
	hashmap_clear(map, destroyer);
}

