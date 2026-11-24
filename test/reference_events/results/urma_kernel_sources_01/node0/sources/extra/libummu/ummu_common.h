/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_COMMON_H_
#define _UMMU_COMMON_H_

#include <stdint.h>

#define BITS_PER_LONG 64U
#define BITS_PER_LONG_SHIFT 6U
#define BITS_PER_LONG_MASK (BITS_PER_LONG - 1U)
#define GENMASK_ULL(h, l) \
		(((~0ULL) - (1ULL << (uint64_t)(l)) + 1ULL) & \
			(~0ULL >> (BITS_PER_LONG - 1ULL - (uint64_t)(h))))

#define LIKELY(CONDITION) __builtin_expect(!!(CONDITION), 1)
#define UNLIKELY(CONDITION) __builtin_expect(!!(CONDITION), 0)

#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define BITS_TO_LONGS(cnt) DIV_ROUND_UP((cnt), BITS_PER_LONG)
#define FOR_EACH_SET_BIT(bit, addr, size) \
	for ((bit) = find_first_bit((addr), (size)); (bit) < (size); (bit) = find_next_bit((addr), (size), (bit) + 1UL))

static inline void ummu_to_device_wmb(void)
{
	asm volatile("dmb oshst" ::: "memory");
}

static inline void ummu_from_device_rmb(void)
{
	asm volatile("dmb osh" ::: "memory");
}

static inline void __attribute__((always_inline)) ummu_set_bit(uint32_t nr, unsigned long *addr)
{
	addr[nr >> BITS_PER_LONG_SHIFT] |= 1UL << (nr & BITS_PER_LONG_MASK);
}

static inline void __attribute__((always_inline)) ummu_clear_bit(uint32_t nr, unsigned long *addr)
{
	addr[nr >> BITS_PER_LONG_SHIFT] &= ~(1UL << (nr & BITS_PER_LONG_MASK));
}

static inline unsigned long __attribute__((always_inline)) ffz(unsigned long word)
{
	return (unsigned long)((unsigned long)__builtin_ffsl(~(word)) - 1UL);
}

unsigned long find_first_zero_bit(const unsigned long *array, unsigned long size);

unsigned long find_first_bit(const unsigned long *array, unsigned long size);

unsigned long find_next_bit(const unsigned long *array, unsigned long size, unsigned long offset);

int ummu_gen_random(uint32_t *value);

#endif

