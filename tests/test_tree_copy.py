"""Regression (hybrid-d77 VM, found again in au-d77's code path): tar exclusion patterns are
unanchored, so "./dev/*" also dropped every nested directory called dev (a kernel module
directory on Chimera, /usr/include/dev on FreeBSD). Exclusions are now anchored and verify()
compares the whole tree."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from mocinha.core.errors import VerificationError
from mocinha.core.events import EventStream
from mocinha.core.provider import ExecutionContext
from mocinha.providers.deployment.tree_copy import (
    TreeCopyDeploymentProvider, anchored_exclude_args, normalize_pattern, tar_flavour,
)


class TestTreeCopy(unittest.TestCase):
    def test_pattern_handling(self) -> None:
        self.assertEqual(normalize_pattern("./var/cache/pkg/*"), "var/cache/pkg/*")
        self.assertEqual(normalize_pattern("/dev/*"), "dev/*")
        self.assertEqual(anchored_exclude_args("bsd", ["dev/*"]), ["--exclude=^dev/*"])
        self.assertEqual(anchored_exclude_args("gnu", ["dev/*"]), ["--anchored", "--exclude=./dev/*"])
        self.assertEqual(tar_flavour("bsdtar 3.7.4 - libarchive 3.7.4"), "bsd")
        self.assertEqual(tar_flavour("tar (GNU tar) 1.35"), "gnu")
        self.assertIsNone(tar_flavour("busybox tar"))

    @unittest.skipUnless(shutil.which("tar"), "needs tar")
    def test_real_copy_keeps_nested_dirs_named_like_excluded_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            for f in ("dev/null-ish", "tmp/junk", "etc/hostname", "usr/lib/modules/k/kernel/drivers/net/can/dev/can-dev.ko",
                      "usr/include/dev/pci/pcireg.h", "usr/share/doc/tmp/readme", "var/tmp/x", "var/cache/pkg/a.pkg"):
                (src / f).parent.mkdir(parents=True, exist_ok=True)
                (src / f).write_text(f)
            (src / "usr/lib/modules/k/apk-dist").mkdir(parents=True)
            (src / "usr/lib/modules/k/apk-dist/can-dev.ko").hardlink_to(src / "usr/lib/modules/k/kernel/drivers/net/can/dev/can-dev.ko")
            dst.mkdir()
            ctx = ExecutionContext(target_disk="/dev/null", target_mount=str(dst), target_partitions={},
                                   metadata={"install_source": str(src), "install_exclude": ["./var/cache/pkg/*"]})
            provider = TreeCopyDeploymentProvider("tree-copy", EventStream())
            provider.validate(ctx)
            provider.apply(ctx)
            provider.verify(ctx)
            for kept in ("usr/lib/modules/k/kernel/drivers/net/can/dev/can-dev.ko", "usr/include/dev/pci/pcireg.h",
                         "usr/share/doc/tmp/readme", "usr/lib/modules/k/apk-dist/can-dev.ko", "etc/hostname"):
                self.assertTrue((dst / kept).is_file(), kept)
            for dropped in ("dev/null-ish", "tmp/junk", "var/tmp/x", "var/cache/pkg/a.pkg"):
                self.assertFalse((dst / dropped).exists(), dropped)
            (dst / "usr/include/dev/pci/pcireg.h").unlink()
            with self.assertRaises(VerificationError):  # a single missing file is caught
                provider.verify(ctx)


if __name__ == "__main__":
    unittest.main()
