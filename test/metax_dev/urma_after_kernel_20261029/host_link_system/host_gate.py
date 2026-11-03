import socket
import time

expected = b'uccl-system-f87b-start'
deadline = time.monotonic() + 240
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('141.61.17.204', 29766))
    server.listen(2)
    print('HOST_GATE_READY', flush=True)
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            raise TimeoutError('Host gate timed out before launch')
        server.settimeout(remain)
        client, peer = server.accept()
        with client:
            client.settimeout(3)
            payload = client.recv(128)
            if peer[0] == '141.61.17.202' and payload == expected:
                client.sendall(b'ACK')
                print('HOST_GATE_OPEN', flush=True)
                break
