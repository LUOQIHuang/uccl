// SPDX-License-Identifier: MIT
/*
 * Copyright (c) 2025 HiSilicon Technologies Co., Ltd. All rights reserved.
 * THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND,
 * EITHER EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
 * MERCHANTABILITY OR FIT FOR A PARTICULAR PURPOSE.
 */

#include <fcntl.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>

#include "ummu_log.h"
#include "ummu_mapt.h"
#include "ummu_map.h"

static struct ummu_ctx_info g_ummu_ctx = {
	.ctx_mutex = PTHREAD_MUTEX_INITIALIZER,
	.shared_fd = -1,
};

struct ummu_ctx_info *get_ummu_ctx(void)
{
	return &g_ummu_ctx;
}

static void ummu_mapt_info_destroy(void *info)
{
	ummu_mapt_destroy((struct ummu_mapt_info *)info);
}

static int ummu_ctx_init(struct ummu_ctx_info *ctx)
{
	errno = 0;
	ctx->shared_fd = open(TID_DEVICE_NAME, O_RDWR | O_CLOEXEC);
	if (ctx->shared_fd < 0) {
		UMMU_MAPT_ERROR_LOG("Open fd failed, errno = %d.\n", errno);
		return -ENODEV;
	}

	ctx->mapt_map = ummu_map_create();
	if (ctx->mapt_map == NULL) {
		(void)close(ctx->shared_fd);
		ctx->shared_fd = -1;
		UMMU_MAPT_ERROR_LOG("Create mapt map failed.\n");
		return -ENOMEM;
	}

	return 0;
}

static void fork_prepare(void)
{
	pthread_mutex_lock(&g_ummu_ctx.ctx_mutex);
}

static void parent_process_after_fork(void)
{
	pthread_mutex_unlock(&g_ummu_ctx.ctx_mutex);
}

static void child_process_after_fork(void)
{
	pthread_mutex_unlock(&g_ummu_ctx.ctx_mutex);
	if (g_ummu_ctx.mapt_map != NULL) {
		ummu_map_clear(g_ummu_ctx.mapt_map, NULL);
		g_ummu_ctx.mapt_map = NULL;
	}
	if (g_ummu_ctx.shared_fd >= 0) {
		(void)close(g_ummu_ctx.shared_fd);
		g_ummu_ctx.shared_fd = -1;
	}

	if (ummu_ctx_init(&g_ummu_ctx) != 0) {
		UMMU_MAPT_ERROR_LOG("Init context failed.\n");
	}
}

__attribute__((destructor)) static void ummu_uninit(void)
{
	UMMU_MAPT_INFO_LOG("UMMU uninit.\n");
	pthread_mutex_lock(&g_ummu_ctx.ctx_mutex);
	if (g_ummu_ctx.mapt_map != NULL) {
		ummu_map_clear(g_ummu_ctx.mapt_map, ummu_mapt_info_destroy);
		g_ummu_ctx.mapt_map = NULL;
	}
	if (g_ummu_ctx.shared_fd >= 0) {
		(void)close(g_ummu_ctx.shared_fd);
		g_ummu_ctx.shared_fd = -1;
	}
	pthread_mutex_unlock(&g_ummu_ctx.ctx_mutex);
	UMMU_MAPT_INFO_LOG("UMMU lib removed succ.\n");
}

__attribute__((constructor)) static void ummu_init(void)
{
	ummu_init_log_level();

	if (pthread_atfork(fork_prepare, parent_process_after_fork, child_process_after_fork) != 0) {
		UMMU_MAPT_WARN_LOG("Set fork handler failed.\n");
	}
	if (ummu_ctx_init(&g_ummu_ctx) != 0) {
		UMMU_MAPT_ERROR_LOG("Init context failed.\n");
	}
}

