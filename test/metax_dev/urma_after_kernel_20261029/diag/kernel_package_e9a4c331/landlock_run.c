#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/landlock.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <unistd.h>
#ifndef LANDLOCK_ACCESS_FS_REFER
#define LANDLOCK_ACCESS_FS_REFER (1ULL << 13)
#endif
#ifndef LANDLOCK_ACCESS_FS_TRUNCATE
#define LANDLOCK_ACCESS_FS_TRUNCATE (1ULL << 14)
#endif
static int allow_path(int ruleset, const char *path, __u64 rights) {
  int fd = open(path, O_PATH | O_CLOEXEC);
  if (fd < 0) { perror(path); return -1; }
  struct landlock_path_beneath_attr p = {.allowed_access = rights, .parent_fd = fd};
  int ret = syscall(__NR_landlock_add_rule, ruleset, LANDLOCK_RULE_PATH_BENEATH, &p, 0);
  if (ret < 0) perror("landlock_add_rule");
  close(fd);
  return ret;
}
int main(int argc, char **argv) {
  if (argc < 3) { fprintf(stderr, "usage: landlock_run ALLOWED_DIRECTORY COMMAND ...\n"); return 2; }
  int abi = syscall(__NR_landlock_create_ruleset, NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
  if (abi < 3) { fprintf(stderr, "LANDLOCK_ABI_TOO_OLD_OR_UNAVAILABLE=%d errno=%d\n", abi, errno); return 3; }
  __u64 rights = LANDLOCK_ACCESS_FS_WRITE_FILE | LANDLOCK_ACCESS_FS_REMOVE_DIR |
      LANDLOCK_ACCESS_FS_REMOVE_FILE | LANDLOCK_ACCESS_FS_MAKE_CHAR |
      LANDLOCK_ACCESS_FS_MAKE_DIR | LANDLOCK_ACCESS_FS_MAKE_REG |
      LANDLOCK_ACCESS_FS_MAKE_SOCK | LANDLOCK_ACCESS_FS_MAKE_FIFO |
      LANDLOCK_ACCESS_FS_MAKE_BLOCK | LANDLOCK_ACCESS_FS_MAKE_SYM |
      LANDLOCK_ACCESS_FS_REFER | LANDLOCK_ACCESS_FS_TRUNCATE;
  struct landlock_ruleset_attr attr = {.handled_access_fs = rights};
  int ruleset = syscall(__NR_landlock_create_ruleset, &attr, sizeof(attr), 0);
  if (ruleset < 0) { perror("landlock_create_ruleset"); return 4; }
  if (allow_path(ruleset, argv[1], rights) || allow_path(ruleset, "/dev/null", LANDLOCK_ACCESS_FS_WRITE_FILE)) return 5;
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || syscall(__NR_landlock_restrict_self, ruleset, 0)) { perror("landlock_restrict_self"); return 6; }
  close(ruleset);
  errno = 0;
  int forbidden = open("/etc/passwd", O_WRONLY | O_CLOEXEC);
  if (forbidden >= 0 || errno != EACCES) {
    if (forbidden >= 0) close(forbidden);
    fprintf(stderr, "LANDLOCK_OUTSIDE_WRITE_NOT_DENIED errno=%d\n", errno);
    return 7;
  }
  char own[4096];
  if (snprintf(own, sizeof(own), "%s/.landlock_test_%ld", argv[1], (long)getpid()) >= (int)sizeof(own)) return 8;
  int good = open(own, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
  if (good < 0) { perror("landlock allowed test"); return 8; }
  close(good);
  if (unlink(own)) { perror("landlock unlink test"); return 8; }
  int nullfd = open("/dev/null", O_WRONLY | O_CLOEXEC);
  if (nullfd < 0) { perror("landlock dev null test"); return 8; }
  close(nullfd);
  fprintf(stderr, "LANDLOCK_OK abi=%d outside_write_denied own_write_allowed\n", abi);
  execvp(argv[2], argv + 2);
  perror("execvp"); return 9;
}
