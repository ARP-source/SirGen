from backend.metrics import BytesOutCounter, read_tx_bytes

HEADER = "Inter-|   Receive |  Transmit\n face |bytes packets|bytes packets\n"


def dev(lo, eth):
    return (HEADER + f"    lo: 100 1 0 0 0 0 0 0 {lo} 1 0 0 0 0 0 0\n"
            f"  eth0: 200 2 0 0 0 0 0 0 {eth} 2 0 0 0 0 0 0\n")


def test_counts_non_loopback_tx_since_start(tmp_path):
    p = tmp_path / "dev"
    p.write_text(dev(1000, 5000))
    c = BytesOutCounter(p)
    assert c.read() == 0
    p.write_text(dev(9000, 5600))
    assert c.read() == 600  # loopback ignored


def test_missing_proc_returns_none(tmp_path):
    assert read_tx_bytes(tmp_path / "absent") is None
    assert BytesOutCounter(tmp_path / "absent").read() is None


def dev3(wifi, cable):
    return (HEADER + "    lo: 100 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0\n"
            f"  wlan0: 200 2 0 0 0 0 0 0 {wifi} 2 0 0 0 0 0 0\n"
            f"  eth0: 200 2 0 0 0 0 0 0 {cable} 2 0 0 0 0 0 0\n")


def test_only_the_named_interfaces_are_counted_and_the_note_names_them(tmp_path, monkeypatch):
    p = tmp_path / "dev"
    p.write_text(dev3(1000, 1000))
    monkeypatch.setenv("SENTINEL_BYTES_OUT_IFACES", "wlan0, usb9")
    c = BytesOutCounter(p)
    p.write_text(dev3(1000, 90000))  # traffic on the direct cable only
    assert c.read() == 0
    p.write_text(dev3(1040, 95000))
    assert c.read() == 40
    assert "wlan0, usb9" in c.note() and "not present: usb9" in c.note() and "not counted" in c.note()
    monkeypatch.delenv("SENTINEL_BYTES_OUT_IFACES")
    every = BytesOutCounter(p)
    assert every.interfaces is None and "all non-loopback" in every.note() and "eth0" in every.note()
