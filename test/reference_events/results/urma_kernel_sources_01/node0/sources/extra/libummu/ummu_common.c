// SPDX-License-Identifier: MIT
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#include <fcntl.h>
#include <errno.h>
#include <unistd.h>

#include "ummu_log.h"
#include "ummu_common.h"

#define UMMU_URANDOM_DEV "/dev/urandom"

int ummu_gen_random(uint32_t *value)
{
	uint32_t random_val = 0;
	int fd, ret;

	fd = open(UMMU_URANDOM_DEV, O_RDONLY);
	if (fd < 0) {
		UMMU_MAPT_ERROR_LOG("Open random fd failed, errno = %d.\n", errno);
		return -errno;
	}

	ret = read(fd, &random_val, sizeof(uint32_t));
	if (ret != sizeof(uint32_t)) {
		UMMU_MAPT_ERROR_LOG("Get random value failed, ret = %d, errno = %d.\n", ret, errno);
		close(fd);
		return -errno;
	}
	close(fd);
	*value = random_val;
	return 0;
}

unsigned long find_first_zero_bit(const unsigned long *array, unsigned long size)
{
	unsigned long i;
	unsigned long ret = 0;
	unsigned long array_size;

	if (UNLIKELY(size == 0)) {
		return size;
	}

	array_size = BITS_TO_LONGS(size);
	for (i = 0; i < array_size; i++) {
		if (~array[i]) {
			return (ret + ffz(array[i]));
		} else {
			ret += BITS_PER_LONG;
		}
	}

	return size;
}

unsigned long find_first_bit(const unsigned long *array, unsigned long size)
{
	unsigned long i;
	unsigned long ret = 0;
	unsigned long array_size;

	if (UNLIKELY(size == 0)) {
		return size;
	}
	array_size = BITS_TO_LONGS(size);
	for (i = 0; i < array_size; i++) {
		if (array[i] != 0) {
			return (ret + ffz(~array[i]));
		} else {
			ret += BITS_PER_LONG;
		}
	}
	return size;
}

unsigned long find_next_bit(const unsigned long *array, unsigned long size, unsigned long offset)
{
	unsigned long i;
	unsigned long ret;
	unsigned long long_offset;
	unsigned long tmp;
	unsigned long array_size;

	if (UNLIKELY(offset >= size || size == 0)) {
		return size;
	}

	i = offset >> BITS_PER_LONG_SHIFT;
	ret = i * BITS_PER_LONG;
	long_offset = offset % BITS_PER_LONG;
	tmp = array[i] & (~0UL << long_offset);
	if (tmp != 0) {
		return (ret + ffz(~tmp));
	} else {
		/* Next unsigned long. */
		ret += BITS_PER_LONG;
		i++;
	}

	array_size = BITS_TO_LONGS(size);
	for (; i < array_size; i++) {
		tmp = array[i];
		if (tmp != 0) {
			return (ret + ffz(~tmp));
		} else {
			ret += BITS_PER_LONG;
		}
	}

	return size;
}

