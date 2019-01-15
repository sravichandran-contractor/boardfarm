# Copyright (c) 2015
#
# All rights reserved.
#
# This file is distributed under the Clear BSD license.
# The full text can be found in LICENSE in the root directory.

import time
import linux_boot
import lib
from devices import board, wan, lan, prompt

class RootFSBootTest(linux_boot.LinuxBootTest):
    '''Flashed image and booted successfully.'''

    def boot(self, reflash=True):
        # start tftpd server on appropriate device
        tftp_servers = [ x['name'] for x in self.config.board['devices'] if 'tftpd-server' in x.get('options', "") ]
        tftp_device = None
        # start all tftp servers for now
        for tftp_server in tftp_servers:
            tftp_device = getattr(self.config, tftp_server)

        dhcp_started = False

        # start dhcp servers
        for device in self.config.board['devices']:
            if 'options' in device and 'dhcp-server' in device['options']:
                getattr(self.config, device['name']).setup_dhcp_server()
                dhcp_started = True

        if not wan and len(tftp_servers) == 0:
            msg = 'No WAN Device or tftp_server defined, skipping flash.'
            lib.common.test_msg(msg)
            self.skipTest(msg)

        # This still needs some clean up, the fall back is to assuming the
        # WAN provides the tftpd server, but it's not always the case
        if wan:
            wan.configure(kind="wan_device", config=self.config.board)
            if tftp_device is None:
                tftp_device = wan

        if wan and not dhcp_started:
            wan.setup_dhcp_server()

        tftp_device.start_tftp_server()

        prov = getattr(self.config, 'provisioner', None)
        if prov is not None:
            prov.tftp_device = tftp_device
            prov.provision_board(self.config.board)

            if hasattr(prov, 'prov_gateway'):
                gw = prov.prov_gateway if wan.gw in prov.prov_network else prov.prov_ip

                for nw in [prov.cm_network, prov.mta_network]:
                    wan.sendline('ip route add %s via %s' % (nw, gw))
                    wan.expect(prompt)

            wan.sendline('ip route')
            wan.expect(prompt)

        if lan:
            lan.configure(kind="lan_device")

        # tftp_device is always None, so we can set it from config
        board.tftp_server = tftp_device.ipaddr
        # then these are just hard coded defaults
        board.tftp_port = 22
        # but are user/password used for tftp, they are likely legacy and just need to go away
        board.tftp_username = "root"
        board.tftp_password = "bigfoot1"

        board.reset()
        rootfs = None

        # Reflash only if at least one or more of these
        # variables are set, or else there is nothing to do in u-boot
        meta_interrupt = False
        if self.config.META_BUILD and not board.flash_meta_booted:
            meta_interrupt = True
        if reflash and (meta_interrupt or self.config.ROOTFS or\
                            self.config.KERNEL or self.config.UBOOT):
            # Break into U-Boot, set environment variables
            board.wait_for_boot()
            board.setup_uboot_network(tftp_device.gw)
            if self.config.META_BUILD:
                for attempt in range(3):
                    try:
                        board.flash_meta(self.config.META_BUILD, wan, lan)
                        break
                    except Exception as e:
                        print(e)
                        tftp_device.restart_tftp_server()
                        board.reset(break_into_uboot=True)
                        board.setup_uboot_network(tftp_device.gw)
                else:
                    raise Exception('Error during flashing...')
            if self.config.UBOOT:
                board.flash_uboot(self.config.UBOOT)
            if self.config.ROOTFS:
                # save filename for cases where we didn't flash it
                # but will use it later to load from memory
                rootfs = board.flash_rootfs(self.config.ROOTFS)
            if self.config.NFSROOT:
                board.prepare_nfsroot(self.config.NFSROOT)
            if self.config.KERNEL:
                board.flash_linux(self.config.KERNEL)
            # Boot from U-Boot to Linux
            board.boot_linux(rootfs=rootfs, bootargs=self.config.bootargs)
        if hasattr(board, "pre_boot_linux"):
            board.pre_boot_linux(wan=wan, lan=lan)
        board.linux_booted = True
        board.wait_for_linux()
        if self.config.META_BUILD and board.flash_meta_booted:
            board.flash_meta(self.config.META_BUILD, wan, lan)
        linux_booted_seconds_up = board.get_seconds_uptime()
        # Retry setting up wan protocol
        if self.config.setup_device_networking:
            for i in range(2):
                time.sleep(10)
                try:
                    if "pppoe" in self.config.WAN_PROTO:
                        wan.turn_on_pppoe()
                    board.config_wan_proto(self.config.WAN_PROTO)
                    break
                except:
                    print("\nFailed to check/set the router's WAN protocol.")
                    pass
            board.wait_for_network()
        board.wait_for_mounts()

        if self.config.setup_device_networking:
            # Router mac addresses are likely to change, so flush arp
            if lan:
                lan.ip_neigh_flush()
            if wan:
                wan.ip_neigh_flush()

            # Clear default routes perhaps left over from prior use
            if lan:
                lan.sendline('\nip -6 route del default')
                lan.expect(prompt)
            if wan:
                wan.sendline('\nip -6 route del default')
                wan.expect(prompt)

        # Give other daemons time to boot and settle
        if self.config.setup_device_networking:
            for i in range(5):
                board.get_seconds_uptime()
                time.sleep(5)

        try:
            board.sendline("passwd")
            board.expect("password:", timeout=8)
            board.sendline("password")
            board.expect("password:")
            board.sendline("password")
            board.expect(prompt)
        except:
            print("WARNING: Unable to set root password on router.")

        board.sendline('cat /proc/cmdline')
        board.expect(prompt)
        board.sendline('uname -a')
        board.expect(prompt)

        # we can't have random messsages messages
        board.sendline("echo \"1 1 1 7\" > /proc/sys/kernel/printk")
        board.expect(prompt)

        if hasattr(self.config, 'INSTALL_PKGS') and self.config.INSTALL_PKGS != "":
            for pkg in self.config.INSTALL_PKGS.split(' '):
                if len(pkg) > 0:
                    board.install_package(pkg)

        # Try to verify router has stayed up (and, say, not suddenly rebooted)
        end_seconds_up = board.get_seconds_uptime()
        print("\nThe router has been up %s seconds." % end_seconds_up)
        if self.config.setup_device_networking:
            assert end_seconds_up > linux_booted_seconds_up

        self.logged['boot_time'] = end_seconds_up

        print "#####"
        print board.routing
        print lan
        print self.config.setup_device_networking
        print "#####"
        if board.routing and lan and self.config.setup_device_networking:
            if wan is not None:
                lan.start_lan_client(wan_gw=wan.gw)
            else:
                lan.start_lan_client()

    reflash = False
    reboot = False

    @lib.common.run_once
    def runTest(self):
        if self.__class__.__name__ == "RootFSBootTest":
            self.boot()

    def recover(self):
        if self.__class__.__name__ == "RootFSBootTest":
            try:
                if board.linux_booted:
                    board.sendline('ps auxfw || ps w')
                    board.expect(prompt)
                    board.sendline('iptables -S')
                    board.expect(prompt)
            except:
                pass

            board.close()
            lib.common.test_msg("Unable to boot, skipping remaining tests...")
            return
        try:
            # let user interact with console if test failed
            try:
                board.sendline()
                board.sendline()
                if not self.config.batch:
                    board.interact()
            except:
                pass
            if self.reboot == True and self.reset_after_fail:
                self.boot(self.reflash)
            self.reboot = True
        except Exception as e:
            print("Unable to recover, %s" % e)
            self.assertEqual(1, 0, e)
