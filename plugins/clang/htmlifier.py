import dxr.plugins
import os, sys
import fnmatch
import clang.tokenizers as tokenizers
import urllib, re

class ClangHtmlifier:
  """ Pygmentizer add syntax regions for file """
  def __init__(self, tree, conn, path, text, file_id):
    self.tree    = tree
    self.conn    = conn
    self.path    = path
    self.text    = text
    self.file_id = file_id

  def regions(self):
    # TODO Don't do syntax highlighting here, we have pygments for this
    # but for the moment being pygments is disabled for cpp files as it
    # has an infinite loop, as reported here:
    # https://bitbucket.org/birkenfeld/pygments-main/issue/795/
    tokenizer = tokenizers.CppTokenizer(self.text)
    for token in tokenizer.getTokens():
      if token.token_type == tokenizer.KEYWORD:
        yield (token.start, token.end, 'k')
      elif token.token_type == tokenizer.STRING:
        yield (token.start, token.end, 'str')
      elif token.token_type == tokenizer.COMMENT:
        yield (token.start, token.end, 'c')
      elif token.token_type == tokenizer.PREPROCESSOR:
        yield (token.start, token.end, 'p')


  def refs(self):
    """ Generate reference menues """
    # We'll need this argument for all queries here
    args = (self.file_id,)

    # Extents for functions defined here
    sql = """
      SELECT extent_start, extent_end, fqualname
        FROM functions
       WHERE file_id = ?
    """
    for start, end, fqualname in self.conn.execute(sql, args):
      yield start, end, self.function_menu(fqualname)

    # Extents for variables defined here
    sql = """
      SELECT extent_start, extent_end, vname
        FROM variables
       WHERE file_id = ?
    """
    for start, end, vname in self.conn.execute(sql, args):
      yield start, end, self.variable_menu(vname)

    # Extents for types defined here
    sql = """
      SELECT extent_start, extent_end, tqualname
        FROM types
       WHERE file_id = ?
    """
    for start, end, tqualname in self.conn.execute(sql, args):
      yield start, end, self.type_menu(tqualname)

    # Extents for macros defined here
    sql = """
      SELECT file_line, file_col, macroname
        FROM macros
       WHERE file_id = ?
    """
    for line, col, macroname in self.conn.execute(sql, args):
      # TODO Refactor macro table and remove the (line, col) scheme!
      start = (line, col)
      end   = (line, col + len(macroname))
      yield start, end, self.macro_menu(macroname)

    # Add references to types
    sql = """
      SELECT refs.extent_start, refs.extent_end,
             types.tqualname,
             (SELECT path FROM files WHERE files.ID = types.file_id),
             types.file_line
        FROM types, refs
       WHERE types.tid = refs.refid AND refs.file_id = ?
    """
    for start, end, tqualname, path, line in self.conn.execute(sql, args):
      menu = self.type_menu(tqualname)
      self.add_jump_definition(menu, path, line)
      yield start, end, menu

    # Add references to functions
    sql = """
      SELECT refs.extent_start, refs.extent_end,
             functions.fqualname,
             (SELECT path FROM files WHERE files.ID = functions.file_id),
             functions.file_line
        FROM functions, refs
       WHERE functions.funcid = refs.refid AND refs.file_id = ?
    """
    for start, end, fqualname, path, line in self.conn.execute(sql, args):
      menu = self.function_menu(fqualname)
      self.add_jump_definition(menu, path, line)
      yield start, end, menu

    # Add references to functions
    sql = """
      SELECT refs.extent_start, refs.extent_end,
             variables.vname,
             (SELECT path FROM files WHERE files.ID = variables.file_id),
             variables.file_line
        FROM variables, refs
       WHERE variables.varid = refs.refid AND refs.file_id = ?
    """
    for start, end, vname, path, line in self.conn.execute(sql, args):
      menu = self.variable_menu(vname)
      self.add_jump_definition(menu, path, line)
      yield start, end, menu

    # Add references to functions
    sql = """
      SELECT refs.extent_start, refs.extent_end,
             macros.macroname,
             (SELECT path FROM files WHERE files.ID = macros.file_id),
             macros.file_line
        FROM macros, refs
       WHERE macros.macroid = refs.refid AND refs.file_id = ?
    """
    for start, end, macroname, path, line in self.conn.execute(sql, args):
      menu = self.macro_menu(macroname)
      self.add_jump_definition(menu, path, line)
      yield start, end, menu

    # Hack to add links for #includes
    # TODO This should be handled in the clang extension we don't know the
    # include paths here, and we cannot resolve includes correctly.
    pattern = re.compile('\#[\s]*include[\s]*[<"](\S+)[">]')
    tokenizer = tokenizers.CppTokenizer(self.text)
    for token in tokenizer.getTokens():
      if token.token_type == tokenizer.PREPROCESSOR and 'include' in token.name:
        match = pattern.match(token.name)
        if match is None:
          continue
        inc_name = match.group(1)
        sql = "SELECT path FROM files WHERE path LIKE ?"
        rows = self.conn.execute(sql, ("%%%s" % (inc_name),)).fetchall()

        if rows is None or len(rows) == 0:
          basename = os.path.basename(inc_name)
          rows = self.conn.execute(sql, ("%%/%s" % (basename),)).fetchall()

        if rows is not None and len(rows) == 1:
          path  = rows[0][0]
          start = token.start + match.start(1)
          end   = token.start + match.end(1)
          url   = self.tree.config.wwwroot + '/' + self.tree.name + '/' + path
          menu  = [{
            'text':   "Jump to file",
            'title':  "Jump to what is likely included there",
            'href':   url,
            'icon':   'jump'
          },]
          yield start, end, menu
      else:
        continue

    # Test hack for declaration/definition jumps
    #sql = """
    #  SELECT extent_start, extent_end, defid
    #    FROM decldef
    #   WHERE file_id = ?
    #"""
    #

  def search(self, query):
    """ Auxiliary function for getting the search url for query """
    url = self.tree.config.wwwroot + "/search?tree=" + self.tree.name
    url += "&q=" + urllib.quote(query)
    return url


  def add_jump_definition(self, menu, path, line):
    """ Add a jump to definition to the menu """
    # Definition url
    url = self.tree.config.wwwroot + '/' + self.tree.name + '/' + path
    url += "#l%s" % line
    menu.insert(0, { 
      'text':   "Jump to definition",
      'title':  "Jump to the definition in '%s'" % os.path.basename(path),
      'href':   url,
      'icon':   'jump'
    })

  def type_menu(self, tqualname):
    """ Build menu for type """
    menu = []
    # Things we can do with tqualname
    menu.append({
      'text':   "Find sub classes",
      'title':  "Find sub classes of this class",
      'href':   self.search("+derived:%s" % tqualname),
      'icon':   'type'
    })
    menu.append({
      'text':   "Find base classes",
      'title':  "Find base classes of this class",
      'href':   self.search("+bases:%s" % tqualname),
      'icon':   'type'
    })
    menu.append({
      'text':   "Find members",
      'title':  "Find members of this class",
      'href':   self.search("+member:%s" % tqualname),
      'icon':   'members'
    })
    menu.append({
      'text':   "Find references",
      'title':  "Find references to this class",
      'href':   self.search("+type-ref:%s" % tqualname),
      'icon':   'reference'
    })
    return menu


  def variable_menu(self, vname):
    """ Build menu for a variable """
    menu = []
    # Well, what more than references can we do?
    menu.append({
      'text':   "Find references",
      'title':  "Find reference to this variable",
      'href':   self.search("+var-ref:%s" % vname),
      'icon':   'field'
    })
    # TODO Investigate whether assignments and usages is possible and useful?
    return menu


  def macro_menu(self, macroname):
    menu = []
    # Things we can do with macros
    self.tree.config.wwwroot
    menu.append({
      'text':   "Find references",
      'title':  "Find references to macros with this name",
      'href':    self.search("+macro-ref:%s" % macroname),
      'icon':   'reference'
    })
    return menu


  def function_menu(self, fqualname):
    """ Build menu for a function """
    menu = []
    # Things we can do with qualified name
    menu.append({
      'text':   "Find callers",
      'title':  "Find functions that calls this function",
      'href':   self.search("+callers:%s" % fqualname),
      'icon':   'method'
    })
    menu.append({
      'text':   "Find callees",
      'title':  "Find functions that are called by this function",
      'href':   self.search("+called-by:%s" % fqualname),
      'icon':   'method'
    })
    menu.append({
      'text':   "Find references",
      'title':  "Find references of this function",
      'href':   self.search("+function-ref:%s" % fqualname),
      'icon':   'reference'
    })
    #TODO Jump between declaration and definition
    return menu


  def annotations(self):
    icon = "background-image: url('%s/static/icons/warning.png');" % self.tree.config.wwwroot
    sql = "SELECT wmsg, file_line FROM warnings WHERE file_id = ?"
    for msg, line in self.conn.execute(sql, (self.file_id,)):
      yield line, {
        'title': msg,
        'class': "note note-warning",
        'style': icon
      }

  def links(self):
    # For each type add a section with members
    sql = "SELECT tname, tid, file_line, tkind FROM types WHERE file_id = ?"
    for tname, tid, tline, tkind in self.conn.execute(sql, (self.file_id,)):
      if len(tname) == 0: continue
      links = []
      links += list(self.member_functions(tid))
      links += list(self.member_variables(tid))

      # Sort them by line
      links = sorted(links, key = lambda link: link[1])

      # Make sure we have a sane limitation of tkind
      if tkind not in ('class', 'struct', 'enum', 'union'):
        print >> sys.stderr, "tkind '%s' was replaced for 'type'!" % tkind
        tkind = 'type'

      # Add the outer type as the first link
      links.insert(0, (tkind, tname, "#l%s" % tline))

      # Now return the type
      yield (30, tname, links)

    # Add all macros to the macro section
    links = []
    sql = "SELECT macroname, file_line FROM macros WHERE file_id = ?"
    for macro, line in self.conn.execute(sql, (self.file_id,)):
      links.append(('macro', macro, "#l%s" % line))
    if links:
      yield (100, "Macros", links)


  def member_functions(self, tid):
    """ Fetch member functions given a type id """
    sql = """
      SELECT fname, file_line
      FROM functions
      WHERE file_id = ? AND scopeid = ?
    """
    for fname, line in self.conn.execute(sql, (self.file_id, tid)):
      # Skip nameless things
      if len(fname) == 0: continue
      yield 'method', fname, "#l%s" % line


  def member_variables(self, tid):
    """ Fetch member variables given a type id """
    sql = """
      SELECT vname, file_line
      FROM variables
      WHERE file_id = ? AND scopeid = ?
    """
    for vname, line in self.conn.execute(sql, (self.file_id, tid)):
      # Skip nameless things
      if len(vname) == 0: continue
      yield 'field', vname, "#l%s" % line

_tree = None
_conn = None
def load(tree, conn):
  global _tree, _conn
  _tree = tree
  _conn = conn

#tokenizers = None
_patterns = ('*.c', '*.cc', '*.cpp', '*.h', '*.hpp')
def htmlify(path, text):
  #if not tokenizers:
  #  # HACK around the fact that we can't load modules from plugin folders
  #  # we'll probably need to fix this later,
  #  #tpath = os.path.join(tree.config.plugin_folder, "cxx-clang", "tokenizers.py")
  #  #imp.load_source("tokenizers", tpath)

  fname = os.path.basename(path)
  if any((fnmatch.fnmatchcase(fname, p) for p in _patterns)):
    # Get file_id, skip if not in database
    sql = "SELECT files.ID FROM files WHERE path = ? LIMIT 1"
    row = _conn.execute(sql, (path,)).fetchone()
    if row:
      return ClangHtmlifier(_tree, _conn, path, text, row[0])
  return None


__all__ = dxr.plugins.htmlifier_exports()
