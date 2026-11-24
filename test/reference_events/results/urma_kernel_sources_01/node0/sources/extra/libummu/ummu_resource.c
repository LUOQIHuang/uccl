// SPDX-License-Identifier: MIT
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#include <errno.h>
#include <sys/ioctl.h>

#include "ummu_log.h"
#include "ummu_queue.h"
#include "ummu_resource.h"

void *ummu_get_core_buf(unsigned int tid, enum ummu_buf_mode mode, size_t size, int fd, unsigned long idx)
{
	int prot = PROT_WRITE | PROT_READ;
	int flags = MAP_SHARED;
	void *addr = NULL;

	if (size == 0) {
		return addr;
	}

	switch (mode) {
		case BASE_MODE_ENTRY_BLOCK:
			addr = mmap(NULL, size, prot, flags, fd, UMMU_OFFSET_BLK(0UL, tid));
			break;
		case BASE_MODE_TABLE_BLOCK:
			if (size < BLOCK_SIZE_16K || size > BLOCK_SIZE_2M) {
				return addr;
			}
			addr = mmap(NULL, size, prot, flags, fd, UMMU_OFFSET_BLK(idx, tid));
			break;
		case BASE_MODE_QUEUE:
			addr = mmap(NULL, size, prot, flags, fd, UMMU_OFFSET_QUE(tid));
			break;
		default:
			break;
	}

	return addr;
}

void ummu_free_core_buf(enum ummu_buf_mode mode, void *buf, size_t size)
{
	if (size == 0 || buf == NULL) {
		UMMU_MAPT_ERROR_LOG("size = %zu or buf is not correct!\n", size);
		return;
	}
		
	switch (mode) {
		case BASE_MODE_ENTRY_BLOCK:
		case BASE_MODE_TABLE_BLOCK:
		case BASE_MODE_QUEUE:
			munmap(buf, size);
			break;
		default:
			break;
	}

	return;
}

int ummu_get_tid(int fd, struct ummu_tid_info *info)
{
	if (fd < 0) {
		UMMU_MAPT_ERROR_LOG("Shared fd is invalid.\n");
		return -ENOENT;
	}

	info->dev = (~0UL);

	return ioctl(fd, UMMU_IOCALLOC_TID, info);
}

void ummu_put_tid(int fd, unsigned int tid)
{
	struct ummu_tid_info info = {
		.tid = tid,
	};
	int ret;

	if (fd < 0) {
		UMMU_MAPT_ERROR_LOG("Shared fd is invalid.\n");
		return;
	}

	ret = ioctl(fd, UMMU_IOCFREE_TID, &info);
	if (ret != 0) {
		UMMU_MAPT_ERROR_LOG("Failed to put tid, ret = %d.\n", ret);
	}
}

void ummu_kcmd_plbi(int fd, struct ummu_tid_info *info, uint32_t opcode)
{
	int ret = 0;

	if (fd < 0) {
		UMMU_MAPT_ERROR_LOG("Shared fd is invalid.\n");
		return;
	}

	if (opcode == PLBI_VA_OP) {
		ret = ioctl(fd, UMMU_IOCPLBI_VA, info);
	} else if (opcode == PLBI_ALL_OP) {
		ret = ioctl(fd, UMMU_IOCPLBI_ALL, info);
	} else {
		UMMU_MAPT_WARN_LOG("Invalid plbi opcode.\n");
	}

	if (ret != 0) {
		UMMU_MAPT_ERROR_LOG("Issue opcode = %u plbi failed, ret = %d.\n", opcode, ret);
	}
}

