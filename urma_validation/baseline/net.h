#pragma once
#include <stdint.h>
#include <sys/socket.h>
/* Only bootstrap uses these blocking, exact-length helpers. The benchmark's
 * nonblocking control loop must keep its original read() semantics. */
static void exact_io(int fd, void* data, size_t len, int sending) {
  size_t off = 0;
  while (off < len) {
    ssize_t n = sending ? send(fd, (char*)data + off, len - off, MSG_NOSIGNAL)
                        : recv(fd, (char*)data + off, len - off, 0);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) { fprintf(stderr, "bootstrap IO failed off=%zu len=%zu errno=%d\n", off, len, errno); exit(2); }
    off += (size_t)n;
  }
}
static void send_blob(int fd, void const* p, uint32_t len) {
  uint32_t wire = htonl(len);
  exact_io(fd, &wire, 4, 1);
  exact_io(fd, (void*)p, len, 1);
}
static void* recv_blob(int fd) {
  uint32_t wire;
  exact_io(fd, &wire, 4, 0);
  uint32_t len = ntohl(wire);
  if (!len || len > 65536) { fprintf(stderr, "invalid bootstrap blob size=%u\n", len); exit(2); }
  void* p = malloc(len);
  if (!p) exit(2);
  exact_io(fd, p, len, 0);
  return p;
}
