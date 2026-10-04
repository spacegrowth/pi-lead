"""The vendored bash-write parser (vendor/relay/bash_writes.py): claude-relay's tests of its PURE parser
(tests/test_lib_bash_writes.py at 25936bf, class TestParseShapes, whole, commands and expected values
unchanged), run against the vendored copy; and a byte-for-byte comparison with relay's file when that
checkout exists. relay's TestResolver (its own git/fs resolver) is not carried over: pi-lead does not call
that resolver; its own is tested in tests/test_bashgate.py."""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "vendor" / "relay" / "bash_writes.py"
RELAY_FILE = Path(os.environ.get("PILEAD_RELAY_CHECKOUT", Path.home() / "development" / "claude-relay")) / "lib" / "bash_writes.py"

_spec = importlib.util.spec_from_file_location("pilead_vendored_bash_writes", VENDORED)
bw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bw)


def body(n):
    return "\n".join("line %d" % i for i in range(n))


# ---- pure parser ---------------------------------------------------------------------------------

class TestParseShapes:

    @pytest.mark.parametrize("cmd,expected", [
        ("cat > bin/relay <<EOF\n%s\nEOF" % body(60), [("bin/relay", 60)]),
        ("cat <<'EOF' > out.py\n%s\nEOF" % body(3), [("out.py", 3)]),
        ("cat >> log.txt <<EOF\na\nEOF", [("log.txt", 1)]),
        ("cat <<-END >x\n\tone\n\ttwo\n\tEND", [("x", 2)]),
        ("echo hi > f.txt", [("f.txt", None)]),
        ("echo hi >| f.txt", [("f.txt", None)]),
        ("make &> build.log", [("build.log", None)]),
        ("cat > path", [("path", None)]),
        ("echo x | tee out", [("out", None)]),
        ("echo x | tee -a a b", [("a", None), ("b", None)]),
        ("cat <<EOF | tee out\n1\n2\n3\nEOF", [("out", 3)]),
        ("cat <<EOF | sort | tee out\n1\n2\nEOF", [("out", 2)]),
        ("sed -i 's/a/b/' f.py", [("f.py", None)]),
        ("sed -i '' 's/a/b/' f.py g.py", [("f.py", None), ("g.py", None)]),
        ("sed -i.bak -e 's/a/b/' f.py", [("f.py", None)]),
        ("sed -i .bak 's/a/b/' f.py", [("f.py", None)]),
        ("sed -Ei 's/a/b/' f.py", [("f.py", None)]),
        ("sed --in-place -e s/a/b/ -e s/c/d/ f.py", [("f.py", None)]),
        ("cp a.py b.py", [("b.py", None)]),
        ("cp -r a b dest/", [("dest/", None)]),
        ("mv -f old.py new.py", [("new.py", None)]),
        ("python3 - <<PY\nopen('lib/x.py', 'w').write('hi')\nPY", [("lib/x.py", 1)]),
        ("python - <<'PY'\nimport x\nwith open(\"a.txt\", mode=\"a\") as f:\n  f.write(1)\nPY",
         [("a.txt", 3)]),
        ("python3 -c \"open('z.txt','w').write('1')\"", [("z.txt", None)]),
        ("python3 -c \"from pathlib import Path; Path('q.md').write_text('x')\"",
         [("q.md", None)]),
        ("FOO=1 sudo tee /etc/x < in", [("/etc/x", None)]),
        ("cd lib && cat > a.py <<EOF\nx\nEOF\necho done > b", [("a.py", 1), ("b", None)]),
    ])
    def test_shape(self, cmd, expected):
        assert bw.parse_write_targets(cmd) == expected

    @pytest.mark.parametrize("cmd", [
        "ls -la", "cat README.md", "git status", "grep foo <<< \"$v\"", "sed 's/a/b/' f.py",
        "python3 -c \"open('z.txt').read()\"", "python3 -c \"open('z.txt', 'r')\"",
        "python3 script.py", "cp onlyone", "cp -t dir a b", "echo 2>&1", "diff <(a) <(b)",
        "wc -l < in.txt", "", "   ", "bash -c 'echo x > y'",
    ])
    def test_no_write(self, cmd):
        assert bw.parse_write_targets(cmd) == []

    def test_heredoc_body_is_not_parsed_as_commands(self):
        cmd = "cat > a.md <<EOF\necho nope > b.txt\nsed -i s/x/y/ c\nEOF"
        assert bw.parse_write_targets(cmd) == [("a.md", 2)]

    def test_two_heredocs_consumed_in_order(self):
        cmd = "cat > a <<A\n1\nA\ncat > b <<B\n1\n2\n3\nB"
        assert bw.parse_write_targets(cmd) == [("a", 1), ("b", 3)]

    @pytest.mark.parametrize("bad", [None, 5, ["cat", ">", "x"], {"a": 1}, "echo 'unterminated > x",
                                     "cat <<EOF > x\nno terminator", "\x00\x00"])
    def test_never_raises(self, bad):
        assert isinstance(bw.parse_write_targets(bad), list)

    def test_unterminated_heredoc_still_counts_body(self):
        assert bw.parse_write_targets("cat <<EOF > x\na\nb") == [("x", 2)]


# ---- the vendored file is relay's, unchanged -----------------------------------------------------

def test_module_is_the_vendored_file():
    assert Path(bw.__file__).resolve() == VENDORED.resolve()


@pytest.mark.skipif(not RELAY_FILE.is_file(), reason="no claude-relay checkout at " + str(RELAY_FILE))
def test_vendored_file_is_byte_identical_to_relay():
    assert VENDORED.read_bytes() == RELAY_FILE.read_bytes()
