"""Static input for S1; this file is never executed by the scanner."""
import subprocess
import shutil


def make_image(image):
    shutil.which("ninja")
    subprocess.run(["mkfs.vfat", "-F", "32", image], check=True)
    subprocess.run(["mcopy", "-i", image, "rootfs/*", "::"], check=True)


def dynamic_command(tool, image):
    subprocess.run([tool, image], check=True)


COMMAND_TEXT = 'subprocess.run(["fake-ninja"])'
