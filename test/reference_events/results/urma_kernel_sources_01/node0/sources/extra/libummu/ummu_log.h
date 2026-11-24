/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#ifndef _UMMU_LOG_H_
#define _UMMU_LOG_H_
#include <stdbool.h>
#include <syslog.h>
#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum ummu_vlog_level {
	UMMU_VLOG_LEVEL_EMERG = 0,
	UMMU_VLOG_LEVEL_ALERT = 1,
	UMMU_VLOG_LEVEL_CRIT = 2,
	UMMU_VLOG_LEVEL_ERR = 3,
	UMMU_VLOG_LEVEL_WARNING = 4,
	UMMU_VLOG_LEVEL_NOTICE = 5,
	UMMU_VLOG_LEVEL_INFO = 6,
	UMMU_VLOG_LEVEL_DEBUG = 7,
	UMMU_VLOG_LEVEL_MAX = 8,
} ummu_vlog_level_t;

bool ummu_log_drop(ummu_vlog_level_t level);
void ummu_log(const char *function, int line, ummu_vlog_level_t level, const char *format, ...)
	__attribute__ ((format (gnu_printf, 4, 5)));
void ummu_init_log_level(void);

#define UMMU_LOG(l, ...)						    \
	if (!ummu_log_drop(UMMU_VLOG_LEVEL_##l)) {			      \
		ummu_log(__func__, __LINE__, UMMU_VLOG_LEVEL_##l, __VA_ARGS__);     \
	}

#define UMMU_MAPT_INFO_LOG(...) UMMU_LOG(INFO, __VA_ARGS__)

#define UMMU_MAPT_ERROR_LOG(...) UMMU_LOG(ERR, __VA_ARGS__)

#define UMMU_MAPT_WARN_LOG(...) UMMU_LOG(WARNING, __VA_ARGS__)

#define UMMU_MAPT_DEBUG_LOG(...) UMMU_LOG(DEBUG, __VA_ARGS__)

#ifdef __cplusplus
}
#endif

#endif

