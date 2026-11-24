/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_SYSCALL_H_
#define _UMMU_SYSCALL_H_
#include <sys/mman.h>

#include "ummu_core.h"
#include "ummu_mapt.h"

#define CORE_BUF_CHECK_INVALID(addr) (((addr) == NULL) || ((addr) == MAP_FAILED))
void *ummu_get_core_buf(unsigned int tid, enum ummu_buf_mode mode, size_t size, int fd, unsigned long idx);
void ummu_free_core_buf(enum ummu_buf_mode mode, void *buf, size_t size);

int ummu_get_tid(int fd, struct ummu_tid_info *info);
void ummu_put_tid(int fd, unsigned int tid);
void ummu_kcmd_plbi(int fd, struct ummu_tid_info *info, uint32_t opcode);

#endif

