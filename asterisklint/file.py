# vim: set ts=8 sw=4 sts=4 et ai:
from .defines import ErrorDef, WarningDef
from .where import Where


if 'we_dont_want_two_linefeeds_between_classdefs':  # for flake8
    class E_FILE_UTF8_BAD(ErrorDef):
        message = 'expected UTF-8 encoding, got something else'

    class W_FILE_CTRL_CHAR(WarningDef):
        message = 'unexpected control character found'

    class W_FILE_DOS_EOFCRLF(WarningDef):
        message = 'unexpected trailing CRLF in DOS file format'

    class W_FILE_DOS_BARELF(WarningDef):
        message = 'unexpected bare LF in DOS file format'

    class W_FILE_UNIX_CRLF(WarningDef):
        message = 'unexpected CRLF in UNIX file format'

    class W_FILE_UNIX_NOLF(WarningDef):
        message = 'unexpected line without LF in UNIX file format'

    class W_WSH_EOL(WarningDef):
        message = 'unexpected trailing whitespace'


class BinFileReader(object):
    """
    Reads the binary opened file fp, but may open more files with the
    opener if an #include directive is encountered.
    """
    def __init__(self, opener=(lambda filename: open(filename, 'rb'))):
        self._files = []
        self._generators = []
        self._filenames = []

        self._opener = opener

    def include(self, filename):
        fp = self._opener(filename)

        if hasattr(fp, 'mode'):
            assert 'b' in fp.mode, 'expected binary opened file'

        self._files.append(fp)
        self._generators.append(enumerate(fp))
        self._filenames.append(fp.name)

    def __iter__(self):
        while self._generators:
            try:
                i, line = next(self._generators[-1])
            except StopIteration:
                if hasattr(self._files[-1], 'close'):
                    self._files[-1].close()
                self._files.pop()
                self._generators.pop()
                self._filenames.pop()
            else:
                yield Where(self._filenames[-1], i + 1, line), line


class EncodingReader(object):
    """
    Decodes lines from UTF-8, the one true encoding.
    """
    def __iter__(self):
        for where, data in super(EncodingReader, self).__iter__():
            try:
                data = data.decode('utf-8')
            except UnicodeDecodeError:
                E_FILE_UTF8_BAD(where)
                data = data.decode('cp1252')  # or latin1? or 9?
            yield where, data


class NoCtrlReader(object):
    """
    Checks for unusual control characters.
    """
    # all, except \r (0d), \n (0a), \t (09)
    illegal = set('\x00\x01\x02\x03\x04\x05\x06\x07'
                  '\x08'      '\x0b\x0c'  '\x0e\x0f'
                  '\x10\x11\x12\x13\x14\x15\x16\x17'
                  '\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f')

    def __iter__(self):
        for where, data in super(NoCtrlReader, self).__iter__():
            charset = set(data)
            union = charset & self.illegal
            if union:
                W_FILE_CTRL_CHAR(where)
            yield where, data


class FileformatReader(object):
    """
    TODO: allow one to specify legal line endings (unix vs dos)
    """
    def _pop_fileinfo(self, fileinfo, stop_at):
        while fileinfo and fileinfo[-1][0] != stop_at:
            fn, is_dos, latest_has_lf, last_where = fileinfo.pop()
            assert None not in (is_dos, latest_has_lf)
            if is_dos and latest_has_lf:
                W_FILE_DOS_EOFCRLF(last_where)
            elif not is_dos and not latest_has_lf:
                W_FILE_UNIX_NOLF(last_where)

    def __iter__(self):
        # We store fileinfo in a stack so we can look backwards from
        # includes, so check file endings.
        fileinfo = []  # [[filename, is_dos], ...]

        for where, data in super(FileformatReader, self).__iter__():
            # Keep track of includes.
            if not fileinfo or fileinfo[-1][0] != where.filename:
                # Where we in here already? Then we're backing out of an
                # include.
                if where.filename in [i[0] for i in fileinfo]:
                    self._pop_fileinfo(fileinfo, where.filename)
                # We weren't? Then we're moving into an include.
                else:
                    fileinfo.append([where.filename, None, None, where])
                # Re-set values.
                filename, is_dos, latest_has_lf, where = fileinfo[-1]

            has_crlf = data.endswith('\r\n')
            has_lf = data.endswith('\n')
            fileinfo[-1][2] = has_lf
            fileinfo[-1][3] = where

            if is_dos is None:
                is_dos = (has_crlf or not has_lf)
                fileinfo[-1][1] = is_dos

            if is_dos:
                if has_lf and not has_crlf:
                    W_FILE_DOS_BARELF(where)
            else:
                if has_crlf:
                    W_FILE_UNIX_CRLF(where)

            if has_crlf:
                data = data[0:-2]
            elif has_lf:
                data = data[0:-1]

            yield where, data

        self._pop_fileinfo(fileinfo, None)


class AsteriskCommentReader(object):
    """
    Unescapes backslash escapes and splits the data from the comments.

    TODO: look at: main/config.c: process_text_line()
    TODO: also parse multiline asterisk comments
    """
    @staticmethod
    def simple_comment_split(data):
        try:
            i = data.index(';')
        except ValueError:
            return data, ''
        while i > 0 and data[i - 1] in ' \t':
            i -= 1
        return data[0:i], data[i:]

    def __iter__(self):
        for where, data in super(AsteriskCommentReader, self).__iter__():
            # We cannot escape whitespace, so no need to keep this
            # around.
            if data.endswith(tuple(' \t')):
                W_WSH_EOL(where)
                data = data.rstrip(' \t')

            # Shortcut if we don't do any escaping.
            if '\\' not in data:
                data, comment = self.simple_comment_split(data)
                yield where, data, comment
                continue

            # Asterisk does really poor backslash escaping in the config
            # decoder. What it does amounts to a lookback only:
            # - is it a semi? check previous char for backslash
            # - if no backslash, break here
            # - if backslash, replace both with single semi and continue
            i = 0
            parts = []
            while True:
                try:
                    i = data.index(';')
                except ValueError:
                    parts.append(data)
                    comment = ''
                    break
                else:
                    if i and data[i - 1] == '\\':
                        parts.append(data[0:i - 1] + ';')
                        data = data[i + 1:]
                    else:
                        parts.append(data[0:i])
                        comment = data[i:]
                        break
            data = ''.join(parts)

            # Move all the whitespace at the end of data to comment.
            i = len(data)
            while i > 0 and data[i - 1] in ' \t':
                i -= 1
            comment = data[i:] + comment
            data = data[0:i]

            yield where, data, comment


class FileReader(AsteriskCommentReader, FileformatReader, NoCtrlReader,
                 EncodingReader, BinFileReader):
    pass
