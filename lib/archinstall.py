# author: Le Anh Tai
# email: leanhtai01@gmail.com
# gitHub: https://github.com/leanhtai01
import json
import pathlib
import re
import subprocess

from lib import diskutils, fileutils


class ArchInstall:
    def __init__(self, setting_file_name, live_system=True):
        self.load_settings(setting_file_name)
        self.home_dir = f'/home/{self.settings["username"]}'

        if live_system:
            self.cmd_prefix = ['arch-chroot', '/mnt']
        else:
            self.cmd_prefix = ['sudo']

    def load_settings(self, file_name):
        """load setting from json file"""
        try:
            with open(file_name) as reader:
                self.settings = json.load(reader)
        except FileNotFoundError:
            self.settings = {}

    def install_packages(self, packages):
        """install packages"""
        subprocess.run(
            self.cmd_prefix
            + ['pacman', '-Syu', '--needed', '--noconfirm']
            + packages
        )

    def install_packages_from_file(self, file_name):
        """install packages from file contain packages list"""
        packages = self.get_packages_from_file(file_name)
        self.install_packages(packages)

    def disable_auto_generate_mirrorlist(self):
        """make sure mirrorlist not auto generated"""
        subprocess.run(['systemctl', 'disable', 'reflector.service'])
        subprocess.run(['systemctl', 'disable', 'reflector.timer'])
        subprocess.run(['systemctl', 'stop', 'reflector.service'])
        subprocess.run(['systemctl', 'stop', 'reflector.timer'])

    def connect_to_wifi(self):
        """connect to wifi using iwd"""
        subprocess.run([
            'iwctl',
            f'--passphrase={self.setting["wifi_password"]}',
            'station', f'{self.settings["wifi_device"]}',
            'connect_hidden' if self.settings["is_hidden_wifi"] else 'connect',
            f'{self.settings["wifi_ssid"]}'
        ])

    def update_system_clock(self):
        """update system clock from internet"""
        subprocess.run(['timedatectl', 'set-ntp', 'true'])

    def setup_mirrors(self):
        """setup mirrors"""
        with open('/etc/pacman.d/mirrorlist', 'w') as writer:
            for mirror in self.settings['mirrors']:
                writer.write(mirror + '\n')

    def prepare_disk(self):
        """prepare disk for installation"""
        part_name_dict = diskutils.prepare_unencrypted_layout(
            self.settings['device_to_install'],
            self.settings['size_of_efi_partition'],
            self.settings['size_of_boot_partition'],
            self.settings['size_of_swap_partition']
        )

        self.settings.update(part_name_dict)

        # save new info to settings.json
        with open('settings.json', 'w') as writer:
            json.dump(self.settings, writer, indent=4)

    def install_essential_packages(self):
        """install essential packages using pacstrap"""
        packages = [
            'base',
            'base-devel',
            'linux',
            'linux-headers',
            'linux-firmware',
            'man-pages',
            'man-db',
            'iptables-nft'
        ]

        subprocess.run(['pacstrap', '/mnt'] + packages)

    def configure_fstab(self):
        """configure fstab"""
        with open('/mnt/etc/fstab', 'a') as writer:
            subprocess.run(['genfstab', '-U', '/mnt'], stdout=writer)

    def configure_time_zone(self):
        """configure time zone"""
        subprocess.run(self.cmd_prefix + [
            'ln', '-sf',
            '/usr/share/zoneinfo/Asia/Ho_Chi_Minh', '/etc/localtime'
        ])

        subprocess.run(self.cmd_prefix + [
            'hwclock', '--systohc'
        ])

    def configure_localization(self):
        """configure localization"""
        locale_gen_path = '/mnt/etc/locale.gen'

        fileutils.backup(locale_gen_path)

        with open(locale_gen_path, 'w') as locale_gen_file:
            locale_gen_file.write('en_US.UTF-8 UTF-8' + '\n')

        subprocess.run(self.cmd_prefix + ['locale-gen'])

        with open('/mnt/etc/locale.conf', 'w') as locale_conf_file:
            locale_conf_file.write('LANG=en_US.UTF-8' + '\n')

    def enable_multilib(self):
        """enable multilib"""
        pacman_conf_path = '/mnt/etc/pacman.conf'

        fileutils.backup(pacman_conf_path)

        with open(pacman_conf_path) as reader:
            content = reader.readlines()

        # find the first line contain multilib config
        line_number = content.index('#[multilib]\n')

        # comment out 2 lines contain multilib config
        content[line_number] = content[line_number].replace('#', '')
        line_number += 1  # to comment next line
        content[line_number] = content[line_number].replace('#', '')

        # save changes
        with open(pacman_conf_path, 'w') as writer:
            writer.writelines(content)

    def configure_network(self):
        """configure network"""
        hostname = self.settings['hostname']

        with open('/mnt/etc/hostname', 'w') as hostname_file:
            hostname_file.write(f'{hostname}\n')

        with open('/mnt/etc/hosts', 'a') as hosts_file:
            hosts_file.write('127.0.0.1\tlocalhost\n')
            hosts_file.write('::1\tlocalhost\n')
            hosts_file.write(
                f'127.0.1.1\t{hostname}.localdomain\t{hostname}\n'
            )

        subprocess.run(self.cmd_prefix + [
            'pacman', '-Syu', '--needed', '--noconfirm', 'networkmanager'
        ])

        subprocess.run(self.cmd_prefix + [
            'systemctl', 'enable', 'NetworkManager'
        ])

    def set_root_password(self):
        """setup root password"""
        password = (self.settings['root_password'] + '\n') * 2
        subprocess.run(self.cmd_prefix + ['passwd'], input=password.encode())

    def add_normal_user(self):
        """add normal user"""
        real_name = self.settings['user_real_name']
        username = self.settings['username']
        password = (self.settings['user_password'] + '\n') * 2
        user_groups = self.settings['user_groups']

        subprocess.run(self.cmd_prefix + [
            'useradd',
            '-G', ','.join(user_groups),
            '-s', '/bin/bash',
            '-m', f'{username}',
            '-d', f'/home/{username}',
            '-c', f'{real_name}'
        ])

        subprocess.run(
            self.cmd_prefix + ['passwd', f'{username}'],
            input=password.encode()
        )

    def allow_user_in_wheel_group_execute_any_command(self):
        """allow user in wheel group execute any command"""
        sudoers_path = '/mnt/etc/sudoers'

        fileutils.backup(sudoers_path)

        fileutils.multiple_replace_in_line(
            sudoers_path,
            rf'^{re.escape("# %wheel ALL=(ALL:ALL) ALL")}.*',
            [('# ', '')]
        )

    def increase_sudo_timestamp_timeout(self):
        """reduce the number of times re-enter password using sudo"""
        sudoers_path = '/mnt/etc/sudoers'

        fileutils.backup(sudoers_path)

        with open(sudoers_path, 'a') as writer:
            writer.write('\n## Set sudo timestamp timeout\n')
            writer.write('Defaults timestamp_timeout=20\n')

    def setup_hibernation(self):
        """setup hibernation"""
        mkinitcpio_config_path = '/mnt/etc/mkinitcpio.conf'

        fileutils.backup(mkinitcpio_config_path)

        fileutils.multiple_replace_in_line(
            mkinitcpio_config_path,
            rf'^{re.escape("HOOKS")}.*',
            [('filesystems', 'filesystems resume')]
        )

        subprocess.run(self.cmd_prefix + [
            'mkinitcpio', '-p', 'linux'
        ])

    def get_uuid(self, partition):
        """get partition's UUID"""
        output = subprocess.run(self.cmd_prefix + [
            'blkid', '-s', 'UUID', '-o', 'value', f'/dev/{partition}'
        ], capture_output=True)

        uuid = output.stdout.decode().strip()

        return uuid

    def configure_systemd_bootloader(self):
        """configure systemd bootloader"""
        self.install_packages(['efibootmgr', 'intel-ucode'])

        subprocess.run(self.cmd_prefix + [
            'bootctl', '--esp-path=/efi', '--boot-path=/boot', 'install'
        ])

        loader_conf_path = '/mnt/efi/loader/loader.conf'
        with open(loader_conf_path, 'w') as loader_conf_file:
            loader_conf_file.write('default archlinux\n')
            loader_conf_file.write('timeout 5\n')
            loader_conf_file.write('console-mode keep\n')
            loader_conf_file.write('editor no\n')

        root_uuid = self.get_uuid(self.settings['root_part_name'])
        swap_uuid = self.get_uuid(self.settings['swap_part_name'])
        archlinux_conf_path = '/mnt/boot/loader/entries/archlinux.conf'
        with open(archlinux_conf_path, 'w') as archlinux_conf_file:
            archlinux_conf_file.write('title Arch Linux\n')
            archlinux_conf_file.write('linux /vmlinuz-linux\n')
            archlinux_conf_file.write('initrd /intel-ucode.img\n')
            archlinux_conf_file.write('initrd /initramfs-linux.img\n')
            archlinux_conf_file.write(
                f'options root=UUID={root_uuid} ' +
                f'resume=UUID={swap_uuid} rw\n'
            )

    def get_packages_from_file(self, file_path):
        """get packages from file"""
        with open(file_path) as reader:
            packages = reader.read()

        return packages.splitlines()

    def install_intel_drivers(self):
        """install gpu drivers"""
        self.install_packages_from_file('packages_info/intel.txt')

    def install_pipewire(self):
        """install pipewire"""
        self.install_packages_from_file('packages_info/pipewire.txt')

    def install_gnome_de(self):
        """install GNOME DE"""
        self.install_packages_from_file('packages_info/gnome_de.txt')

    def install_kde_plasma_de(self):
        """install KDE Plasma DE"""
        self.install_packages_from_file('packages_info/kde_plasma_de.txt')

    def enable_bluetooth_service(self):
        """enable bluetooth service"""
        subprocess.run(self.cmd_prefix + [
            'systemctl', 'enable', 'bluetooth'
        ])

    def configure_display_manager(self, display_manager):
        """configure display manager"""
        match display_manager:
            case 'gdm':
                subprocess.run(self.cmd_prefix + [
                    'systemctl', 'enable', 'gdm'
                ])
            case 'sddm':
                subprocess.run(self.cmd_prefix + [
                    'systemctl', 'enable', 'sddm'
                ])

    def install_fonts(self):
        """install fonts"""
        self.install_packages_from_file('packages_info/fonts.txt')

    def install_browsers(self):
        """install browsers"""
        self.install_packages_from_file('packages_info/browsers.txt')

    def install_core_programming(self):
        """install core programming packages"""
        self.install_packages_from_file('packages_info/core_programming.txt')

    def install_core_tools(self):
        """install core tools"""
        self.install_packages_from_file('packages_info/core_tools.txt')

    def install_virtualbox(self):
        """install virtualbox"""
        self.install_packages([
            'virtualbox', 'virtualbox-guest-iso', 'virtualbox-host-dkms'
        ])

        subprocess.run(self.cmd_prefix + [
            'gpasswd', '-a', f'{self.settings["username"]}', 'vboxusers'
        ])

    def install_docker(self):
        """install docker"""
        self.install_packages(['docker', 'docker-compose'])

        subprocess.run(self.cmd_prefix + [
            'gpasswd', '-a', f'{self.settings["username"]}', 'docker'
        ])

    def install_java_programming(self):
        """install java programming"""
        self.install_packages_from_file('packages_info/java_programming.txt')

    def install_javascript_programming(self):
        """install javascript programming"""
        self.install_packages_from_file(
            'packages_info/javascript_programming.txt'
        )

    def install_multimedia(self):
        """install multimedia"""
        self.install_packages_from_file('packages_info/multimedia.txt')

    def install_office(self):
        """install office"""
        self.install_packages_from_file('packages_info/office.txt')

    def configure_as_virtualbox_guest(self):
        """configure as VirtualBox guest"""
        self.install_packages(['virtualbox-guest-utils'])

        subprocess.run(self.cmd_prefix + [
            'systemctl', 'enable', 'vboxservice'
        ])

        subprocess.run(self.cmd_prefix + [
            'gpasswd', '-a', f'{self.settings["username"]}', 'vboxsf'
        ])

    def add_gnome_shortcut(self, name, key_binding, command):
        """add a GNOME shortcut"""
        SCHEMA_TO_LIST = 'org.gnome.settings-daemon.plugins.media-keys'
        SCHEMA_TO_ITEM = (
            'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding'
        )
        PATH_TO_CUSTOM_KEY = (
            '/org/gnome/settings-daemon/plugins/media-keys'
            '/custom-keybindings/custom'
        )

        # from existing custom shortcuts, determine index of new shortcut
        cmd_result = subprocess.run([
            'gsettings', 'get', f'{SCHEMA_TO_LIST}', 'custom-keybindings'
        ], capture_output=True)

        # get custom shortcuts path list in string
        path_list = cmd_result.stdout.decode()

        if path_list.strip() == '@as []':  # no custom shorcut
            index = 0
        else:
            # get a list of number represent index of custom shortcuts
            index_list = re.findall(r'\d+', path_list)

            index = len(index_list)

        subprocess.run([
            'gsettings', 'set',
            f'{SCHEMA_TO_ITEM}:{PATH_TO_CUSTOM_KEY}{index}/',
            'name', f'"{name}"'
        ])

        subprocess.run([
            'gsettings', 'set',
            f'{SCHEMA_TO_ITEM}:{PATH_TO_CUSTOM_KEY}{index}/',
            'binding', f'"{key_binding}"'
        ])

        subprocess.run([
            'gsettings', 'set',
            f'{SCHEMA_TO_ITEM}:{PATH_TO_CUSTOM_KEY}{index}/',
            'command', f'"{command}"'
        ])

        # determine new path_list
        if index == 0:
            path_list = f"['{PATH_TO_CUSTOM_KEY}{index}/']"
        else:
            # -2 here mean ignore the last character ] in old path_list
            path_list = path_list[:-2] + f", '{PATH_TO_CUSTOM_KEY}{index}/']"

        subprocess.run([
            'gsettings', 'set', f'{SCHEMA_TO_LIST}',
            'custom-keybindings', f'{path_list}'
        ])

    def is_package_installed(self, package_name):
        """check whether package is installed"""
        cmd_result = subprocess.run([
            'pacman', '-Qi', package_name
        ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        return True if cmd_result.returncode == 0 else False

    def is_flatpak_package_installed(self, package_id):
        """check whether flatpak package is installed"""
        self.install_packages(['flatpak'])

        cmd_result = subprocess.run([
            'flatpak', 'info', package_id
        ], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)

        return True if cmd_result.returncode == 0 else False

    def configure_pipewire(self):
        """configure sound server"""
        self.install_packages_from_file('packages_info/pipewire.txt')

        pathlib.Path(f'{self.home_dir}/.config').mkdir(exist_ok=True)

        subprocess.run([
            'cp', '-r', '/usr/share/pipewire', f'{self.home_dir}/.config'
        ])

        fileutils.multiple_replace_in_line(
            f'{self.home_dir}/.config/pipewire/client.conf',
            rf'.*{re.escape("#resample.quality")}.*',
            [('#', ''), ('4', '15')]
        )

        fileutils.multiple_replace_in_line(
            f'{self.home_dir}/.config/pipewire/pipewire-pulse.conf',
            rf'.*{re.escape("#resample.quality")}.*',
            [('#', ''), ('4', '15')]
        )

        fileutils.multiple_replace_in_line(
            f'{self.home_dir}/.config/pipewire/media-session.d/' +
            'media-session.conf',
            rf'.*{re.escape("suspend-node")}.*',
            [('suspend-node', '#suspend-node')]
        )

        subprocess.run([
            'systemctl', '--user', 'enable', 'pipewire-media-session.service'
        ])

    def configure_git(self):
        """configure git"""
        # make sure git is installed before configure
        self.install_packages(['git'])

        subprocess.run([
            'git', 'config', '--global', 'user.email',
            f'{self.settings["user_email"]}'
        ])

        subprocess.run([
            'git', 'config', '--global', 'user.name',
            f'{self.settings["username"]}'
        ])

        subprocess.run([
            'git', 'config', '--global', 'credential.helper', 'store'
        ])

    def install_base_system(self):
        """install base system"""
        self.disable_auto_generate_mirrorlist()
        self.update_system_clock()
        self.setup_mirrors()
        self.prepare_disk()
        self.install_essential_packages()
        self.configure_fstab()
        self.configure_time_zone()
        self.configure_localization()
        self.enable_multilib()
        self.configure_network()
        self.set_root_password()
        self.add_normal_user()
        self.allow_user_in_wheel_group_execute_any_command()
        self.increase_sudo_timestamp_timeout()
        self.setup_hibernation()
        self.configure_systemd_bootloader()