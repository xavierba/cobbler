"""Cobbler action to create bootable Grub2 images.

This action calls grub2-mkimage for all bootloader formats configured in
Cobbler's settings. See man(1) grub2-mkimage for available formats.
"""
import logging
import pathlib
import subprocess
import sys
import typing

from cobbler import utils


# NOTE: does not warrant being a class, but all Cobbler actions use a class's ".run()" as the entrypoint
class MkLoaders:
    """
    Action to create bootloader images.
    """

    def __init__(self, api):
        """
        MkLoaders constructor.

        :param api: CobblerAPI instance for accessing settings
        """
        self.logger = logging.getLogger()
        self.bootloaders_dir = pathlib.Path(api.settings().bootloaders_dir)
        # GRUB 2
        self.grub2_mod_dir = pathlib.Path(api.settings().grub2_mod_dir)
        self.boot_loaders_formats: typing.Dict = api.settings().bootloaders_formats
        self.modules: typing.List = api.settings().bootloaders_modules
        # Syslinux
        self.syslinux_dir = pathlib.Path(api.settings().syslinux_dir)
        self.syslinux_links = {
            self.syslinux_dir.joinpath(f): self.bootloaders_dir.joinpath(f)
            for f in ["pxelinux.0", "menu.c32", "ldlinux.c32", "memdisk"]
        }

    def run(self):
        """
        Run GrubImages action. If the files or executables for the bootloader is not available we bail out and skip the
        creation after it is logged that this is not available.
        """
        self.create_directories()

        self.make_shim()
        self.make_ipxe()
        self.make_syslinux()
        self.make_grub()

    def make_shim(self):
        """
        Create symlink of the shim bootloader in case it is available on the system.
        """
        if not utils.command_existing("shim-install"):
            self.logger.info("shim-install missing. This means we are probably also missing the file we require. "
                             "Bailing out of linking the shim!")
            return
        symlink(
            pathlib.Path("/usr/share/efi/x86_64/shim.efi"),
            self.bootloaders_dir.joinpath(pathlib.Path("grub/shim.efi")),
            skip_existing=True
        )

    def make_ipxe(self):
        """
        Create symlink of the iPXE bootloader in case it is available on the system.
        """
        if not pathlib.Path("/usr/share/ipxe").exists():
            self.logger.info("ipxe directory did not exist. Bailing out of iPXE setup!")
            return
        symlink(
            pathlib.Path("/usr/share/ipxe/undionly.kpxe"),
            self.bootloaders_dir.joinpath(pathlib.Path("undionly.pxe")),
            skip_existing=True
        )

    def make_syslinux(self):
        """
        Create symlink of the important syslinux bootloader files in case they are available on the system.
        """
        if not utils.command_existing("syslinux"):
            self.logger.info("syslinux command not available. Bailing out of syslinux setup!")
            return
        for target, link in self.syslinux_links.items():
            if link.name == "ldlinux.c32" and get_syslinux_version() < 5:
                # This file is only required for Syslinux 5 and newer.
                # Source: https://wiki.syslinux.org/wiki/index.php?title=Library_modules
                self.logger.info('syslinux version 4 detected! Skip making symlink of "ldlinux.c32" file!')
                continue
            symlink(target, link, skip_existing=True)

    def make_grub(self):
        """
        Create symlink of the GRUB 2 bootloader in case it is available on the system. Additionally build the loaders
        for other architectures if the modules to do so are available.
        """
        symlink(
            pathlib.Path("/usr/share/efi/x86_64/grub.efi"),
            self.bootloaders_dir.joinpath(pathlib.Path("grub/grub.efi")),
            skip_existing=True
        )

        if not utils.command_existing("grub2-mkimage"):
            self.logger.info("grub2-mkimage command not available. Bailing out of GRUB2 generation!")
            return

        for image_format, options in self.boot_loaders_formats.items():
            bl_mod_dir = options.get("mod_dir", image_format)
            mod_dir = self.grub2_mod_dir.joinpath(bl_mod_dir)
            if not mod_dir.exists():
                self.logger.info(
                    'GRUB2 modules directory for arch "%s" did no exist. Skipping GRUB2 creation',
                    image_format
                )
                continue
            try:
                mkimage(
                    image_format,
                    self.bootloaders_dir.joinpath("grub", options["binary_name"]),
                    self.modules + options.get("extra_modules", []),
                )
            except subprocess.CalledProcessError:
                self.logger.info('grub2-mkimage failed for arch "%s"! Maybe you did forget to install the grub modules '
                                 'for the architecture?', image_format)
                utils.log_exc()
                # don't create module symlinks if grub2-mkimage is unsuccessful
                continue
            self.logger.info('Successfully built bootloader for arch "%s"!', image_format)

            # Create a symlink for GRUB 2 modules
            # assumes a single GRUB can be used to boot all kinds of distros
            # if this assumption turns out incorrect, individual "grub" subdirectories are needed
            symlink(
                mod_dir,
                self.bootloaders_dir.joinpath("grub", bl_mod_dir),
                skip_existing=True
            )

    def create_directories(self):
        """
        Create the required directories so that this succeeds. If existing, do nothing. This should create the tree for
        all supported bootloaders, regardless of the capabilities to symlink/install/build them.
        """
        if not self.bootloaders_dir.exists():
            raise FileNotFoundError("Main bootloader directory not found! Please create it yourself!")

        grub_dir = self.bootloaders_dir.joinpath("grub")
        if not grub_dir.exists():
            grub_dir.mkdir(mode=0o644)


# NOTE: move this to cobbler.utils?
# cobbler.utils.linkfile does a lot of things, it might be worth it to have a
# function just for symbolic links
def symlink(target: pathlib.Path, link: pathlib.Path, skip_existing: bool = False):
    """Create a symlink LINK pointing to TARGET.

    :param target: File/directory that the link will point to. The file/directory must exist.
    :param link: Filename for the link.
    :param skip_existing: Controls if existing links are skipped, defaults to False.
    :raises FileNotFoundError: ``target`` is not an existing file.
    :raises FileExistsError: ``skip_existing`` is False and ``link`` already exists.
    """

    if not target.exists():
        raise FileNotFoundError(
            f"{target} does not exist, can't create a symlink to it."
        )
    try:
        link.symlink_to(target)
    except FileExistsError:
        if not skip_existing:
            raise


def mkimage(image_format: str, image_filename: pathlib.Path, modules: typing.List):
    """Create a bootable image of GRUB using grub2-mkimage.

    :param image_format: Format of the image that is being created. See man(1)
        grub2-mkimage for a list of supported formats.
    :param image_filename: Location of the image that is being created.
    :param modules: List of GRUB modules to include into the image
    :raises subprocess.CalledProcessError: Error raised by ``subprocess.run``.
    """

    if not image_filename.parent.exists():
        image_filename.parent.mkdir(parents=True)

    cmd = ["grub2-mkimage"]
    cmd.extend(("--format", image_format))
    cmd.extend(("--output", str(image_filename)))
    cmd.append("--prefix=")
    cmd.extend(modules)

    # The Exception raised by subprocess already contains everything useful, it's simpler to use that than roll our
    # own custom exception together with cobbler.utils.subprocess_* functions
    subprocess.run(cmd, check=True)


def get_syslinux_version() -> int:
    """
    This calls syslinux and asks for the version number.

    :return: The major syslinux release number.
    :raises subprocess.CalledProcessError: Error raised by ``subprocess.run`` in case syslinux does not return zero.
    """
    # Example output: "syslinux 4.04  Copyright 1994-2011 H. Peter Anvin et al"
    cmd = ["syslinux", "-v"]
    completed_process = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       encoding=sys.getdefaultencoding())
    output = completed_process.stdout.split()
    return int(float(output[1]))
