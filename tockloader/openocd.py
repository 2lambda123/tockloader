'''
Interface for boards using OpenOCD.

This interface has a special option called `openocd_options` which is just a
list of strings that are interpreted as flags to the OpenOCD class in this file.
These allow individual boards to have custom operations in a semi-reasonable
way. Note, I just made up the string (flag) names; they are not passed to
OpenOCD directly.
'''

import platform
import shlex
import subprocess
import tempfile

from .board_interface import BoardInterface
from .exceptions import TockLoaderException

# global static variable for collecting temp files for Windows
collect_temp_files = []

class OpenOCD(BoardInterface):

	def _run_openocd_commands (self, commands, binary, write=True):
		'''
		- `commands`: String of openocd commands. Use {binary} for where the name
		  of the binary file should be substituted.
		- `binary`: A bytes() object that will be used to write to the board.
		- `write`: Set to true if the command writes binaries to the board. Set
		  to false if the command will read bits from the board.
		'''

		# in Windows, you can't mark delete bc they delete too fast
		delete = platform.system() != 'Windows'
		if self.args.debug:
			delete = False

		if binary or not write:
			temp_bin = tempfile.NamedTemporaryFile(mode='w+b', suffix='.bin', delete=delete)
			if write:
				temp_bin.write(binary)

			temp_bin.flush()

			if platform.system() == 'Windows':
				# For Windows, forward slashes need to be escaped
				temp_bin.name = temp_bin.name.replace('\\', '\\\\\\')
				# For Windows, files need to be manually deleted
				global collect_temp_files
				collect_temp_files += [temp_bin.name]

			# Update the command with the name of the binary file
			commands = commands.format(binary=temp_bin.name)

		# Create the actual openocd command and run it. All of this can be
		# customized if needed for an unusual board.

		# Defaults.
		prefix = ''
		source = 'source [find board/{board}];'.format(board=self.openocd_board)
		cmd_prefix = 'init; reset init; halt;'
		cmd_suffix = ''

		# Do the customizations
		if 'workareazero' in self.openocd_options:
			prefix = 'set WORKAREASIZE 0;'
		if self.openocd_prefix:
			prefix = self.openocd_prefix
		if self.openocd_board == None:
			source = ''
		if 'noreset' in self.openocd_options:
			cmd_prefix = 'init; halt;'
		if 'nocmdprefix' in self.openocd_options:
			cmd_prefix = ''
		if 'resume' in self.openocd_options:
			cmd_suffix = 'soft_reset_halt; resume;'

		openocd_command = 'openocd -c "{prefix} {source} {cmd_prefix} {cmd} {cmd_suffix} exit"'.format(
			prefix=prefix, source=source, cmd_prefix=cmd_prefix, cmd=commands, cmd_suffix=cmd_suffix)

		if self.args.debug:
			print('Running "{}".'.format(openocd_command))

		def print_output (subp):
			response = ''
			if subp.stdout:
				response += subp.stdout.decode('utf-8')
			if subp.stderr:
				response += subp.stderr.decode('utf-8')
			print(response)
			return response

		p = subprocess.run(shlex.split(openocd_command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
		if p.returncode != 0:
			print('ERROR: openocd returned with error code ' + str(p.returncode))
			out = print_output(p)
			if 'Can\'t find board/' in out:
				raise TockLoaderException('ERROR: Cannot find the board configuration file. \
You may need to update OpenOCD to the version in latest git master.')
			raise TockLoaderException('openocd error')
		elif self.args.debug:
			print_output(p)

		# check that there was a JTAG programmer and that it found a device
		stdout = p.stdout.decode('utf-8')
		if 'Error: No J-Link device found.' in stdout:
			raise TockLoaderException('ERROR: Cannot find hardware. Is USB attached?')

		if write == False:
			# Wanted to read binary, so lets pull that
			temp_bin.seek(0, 0)
			return temp_bin.read()

	def flash_binary (self, address, binary):
		'''
		Write using openocd `program` command.
		'''
		# The "normal" flash command uses `program`.
		command = 'program {{binary}} verify {address:#x};'

		# Check if the configuration wants to override the default program command.
		if 'program' in self.openocd_commands:
			command = self.openocd_commands['program']

		# Substitute the key arguments.
		command = command.format(address=address)
		self._run_openocd_commands(command, binary)

	def read_range (self, address, length):
		# The normal read command uses `dump_image`.
		command = 'dump_image {{binary}} {address:#x} {length};'

		# Check if the configuration wants to override the default read command.
		if 'read' in self.openocd_commands:
			command = self.openocd_commands['read']

		# Substitute the key arguments.
		command = command.format(address=address, length=length)

		# Always return a valid byte array (like the serial version does)
		read = bytes()
		result = self._run_openocd_commands(command, None, write=False)
		if result:
			read += result

		# Check to make sure we didn't get too many
		if len(read) > length:
			read = read[0:length]

		return read

	def erase_page (self, address):
		if self.args.debug:
			print('Erasing page at address {:#0x}'.format(address))

		# For some reason on the nRF52840DK erasing an entire page causes
		# previous flash to be reset to 0xFF. This doesn't seem to happen
		# if the binary we write is 512 bytes, so let's just do that. Since
		# we only use erase_page to end the linked-list of apps this will be
		# ok. If we ever actually need to reset an entire page exactly we will
		# have to revisit this.
		command = 'flash fillb {address:#x} 0xff 512;'.format(address=address)

		# Check if the configuration wants to override the default erase command.
		if 'erase' in self.openocd_commands:
			command = self.openocd_commands['erase']

		# Substitute the key arguments.
		command = command.format(address=address)
		self._run_openocd_commands(command, None)

	def determine_current_board (self):
		if self.board and self.arch and self.openocd_board and self.page_size>0:
			# These are already set! Yay we are done.
			return

		# If the user specified a board, use that configuration
		if self.board and self.board in self.KNOWN_BOARDS:
			print('Using known arch and jtag-device for known board {}'.format(self.board))
			board = self.KNOWN_BOARDS[self.board]
			self.arch = board['arch']
			self.openocd_board = board['openocd']
			if 'openocd_options' in board:
				self.openocd_options = board['openocd_options']
			if 'openocd_prefix' in board:
				self.openocd_prefix = board['openocd_prefix']
			if 'openocd_commands' in board:
				self.openocd_commands = board['openocd_commands']
			self.page_size = board['page_size']
			return

		# The primary (only?) way to do this is to look at attributes
		attributes = self.get_all_attributes()
		for attribute in attributes:
			if attribute and attribute['key'] == 'board' and self.board == None:
				self.board = attribute['value']
			if attribute and attribute['key'] == 'arch' and self.arch == None:
				self.arch = attribute['value']
			if attribute and attribute['key'] == 'openocd':
				self.openocd_board = attribute['value']
			if attribute and attribute['key'] == 'pagesize' and self.page_size == 0:
				self.page_size = attribute['value']

		# Check that we learned what we needed to learn.
		if self.board == None or self.arch == None or self.openocd_board == 'cortex-m0' or self.page_size == 0:
			raise TockLoaderException('Could not determine the current board or arch or openocd board name')
