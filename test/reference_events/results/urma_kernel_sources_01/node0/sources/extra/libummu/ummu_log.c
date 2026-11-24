/* SPDX-License-Identifier: MIT */
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#include <errno.h>
#include <stdio.h>
#include <pthread.h>
#include <stdlib.h>

#include "ummu_api.h"
#include "ummu_log.h"


#define MAX_LOG_LEN 256
#define UMMU_LOG_TAG "UMMU"
#define DEFAULT_LOG_NAME "syslog"
#define MAX_BACKEND_NAME_LEN 128

static char g_ummu_log_backend_name[MAX_BACKEND_NAME_LEN] = DEFAULT_LOG_NAME;
static void ummu_default_log_func(int level, char *message)
{
	syslog(level, "%s", message);
}

static ummu_log_backend_t g_ummu_log_func = {
	.name = g_ummu_log_backend_name,
	.emit = ummu_default_log_func,
};
static pthread_mutex_t g_log_backend_lock = PTHREAD_MUTEX_INITIALIZER;
static ummu_vlog_level_t g_loglevel;

inline bool ummu_log_drop(ummu_vlog_level_t level)
{
	return level > g_loglevel;
}

int ummu_log_register_backend(ummu_log_backend_t *backend)
{
	if (backend == NULL || backend->emit == NULL) {
		return -EINVAL;
	}

	UMMU_MAPT_INFO_LOG("register log backend [%s].\n",
		(backend->name != NULL) ? backend->name : "unknown");

	pthread_mutex_lock(&g_log_backend_lock);
	snprintf(g_ummu_log_backend_name, sizeof(g_ummu_log_backend_name),
		 "%s", (backend->name != NULL) ? backend->name : "unknown");
	__atomic_store(&g_ummu_log_func.emit, &backend->emit, __ATOMIC_RELEASE);
	pthread_mutex_unlock(&g_log_backend_lock);

	return 0;
}

void ummu_log_restore_default(void)
{
	typeof(g_ummu_log_func.emit) default_emit = ummu_default_log_func;

	UMMU_MAPT_INFO_LOG("restore default log backend.\n");
	pthread_mutex_lock(&g_log_backend_lock);
	snprintf(g_ummu_log_backend_name, sizeof(g_ummu_log_backend_name),
		 "%s", DEFAULT_LOG_NAME);
	__atomic_store(&g_ummu_log_func.emit, &default_emit, __ATOMIC_RELEASE);
	pthread_mutex_unlock(&g_log_backend_lock);
}

static inline void ummu_log_emit(int level, char *msg)
{
	typeof(g_ummu_log_func.emit) emit = NULL;

	__atomic_load(&g_ummu_log_func.emit, &emit, __ATOMIC_ACQUIRE);
	if (emit != NULL)
		emit(level, msg);
}

void ummu_log(const char *function, int line, ummu_vlog_level_t level, const char *format, ...)
{
	va_list va;
	int ret;

	va_start(va, format);
	char newformat[MAX_LOG_LEN + 1] = {0};
	char logmsg[MAX_LOG_LEN + 1] = {0};

	/* add log head info, "[UMMU_LOG_TAG][function:line] format" */
	ret = snprintf(newformat, MAX_LOG_LEN, "[%s][%s:%d] %s", UMMU_LOG_TAG, function, line, format);
	if (ret <= 0 || ret >= (int)sizeof(newformat)) {
		va_end(va);
		return;
	}

	ret = vsnprintf(logmsg, MAX_LOG_LEN, newformat, va);
	if (ret == -1) {
		(void)printf("logmsg size exceeds MAX_LOG_LEN size : %d\n", MAX_LOG_LEN);
		va_end(va);
		return;
	}

	va_end(va);
	ummu_log_emit((int)level, logmsg);
}

#define DECIMAL 10U
#define LOGLVLLEN 3U
void ummu_init_log_level(void)
{
	FILE *fd = fopen("/usr/lib64/ummu_log_level", "r");
	char buffer[LOGLVLLEN] = { 0 };
	long input_log_level;
	char *end_ptr = NULL;

	g_loglevel = UMMU_VLOG_LEVEL_INFO;
	if (fd == NULL) {
		UMMU_MAPT_WARN_LOG("Use UMMU default loglevel = %u.\n", g_loglevel);
		return;
	}
	if (fread(buffer, sizeof(char), sizeof(buffer) - 1, fd) < 1) {
		(void)fclose(fd);
		UMMU_MAPT_ERROR_LOG("Read ummu_log_level failed, use default loglevel = %u.\n", g_loglevel);
		return;
	}

	errno = 0;
	input_log_level = strtol(buffer, &end_ptr, DECIMAL);
	if (errno == 0 && end_ptr != buffer &&
		input_log_level >= (long)UMMU_VLOG_LEVEL_EMERG &&
		input_log_level < (long)UMMU_VLOG_LEVEL_MAX) {
		g_loglevel = (ummu_vlog_level_t)input_log_level;
	}

	UMMU_MAPT_INFO_LOG("UMMU log level = %u.\n", g_loglevel);
	(void)fclose(fd);
}
