import itertools
import bs4
import re
import string
import pyparsing as pyp
import custom_tags

from custom_tags import _registered_custom_tags
from inline_markdown import inline_markdown_parser

_section_header_token = '----'
_comment_marker_token = '//'
_code_block_marker = "```"

def is_section_header(line):
    if len(line)<4:
        return False
    return line[:4] == _section_header_token

def section_iterator(lines):
    section = []
    for line in lines:
        if is_section_header(line) and section:
            yield section
            section = [line,]
        else:
            section.append(line)

    yield section  
            
def file_iterator(f_md):

    # Read all the lines
    lines = []
    with open(f_md) as FIN:
        for line in FIN:
            if not line.strip():
                continue
            if len(line)>=2 and line.lstrip()[:2] == _comment_marker_token:
                continue
            yield line.rstrip()

########################################################################
    
class tagline(object):
    '''
    Each line is parsed by tokens individually until no preprocessing 
    tokens are left.
    '''

    def __init__(self, line):
  
        self.classnames = []
        self.line = line

        token = lambda c: pyp.Literal(c).suppress()

        g_name = pyp.Word(pyp.alphanums+'-_')
        g_quote = pyp.QuotedString('"')|pyp.QuotedString("'")
        g_header = token('----')+pyp.ZeroOrMore('-').suppress()
        
        g_option_token = (g_name("key") + token('=') +
                          (g_name|g_quote)("value"))
        
        g_option = pyp.nestedExpr(content=pyp.Group(g_option_token))

        g_tag   = (token('@') + g_name('name') +
                   pyp.Optional(g_option)('options'))
                           
        g_classname = token('.') + g_name('name')

        g_format_header = g_header + pyp.ZeroOrMore(g_classname)

        g_format_named_tag = g_tag + pyp.ZeroOrMore(g_classname)
        g_format_div_tag = pyp.OneOrMore(g_classname)
        
        
        g_format = g_format_header | g_format_named_tag | g_format_div_tag
        
        grammar = pyp.Optional(g_format) + pyp.restOfLine('text')

        self.tag_name = None
        self.tag_options = {}

        def parse_tag(tag):
            options = {}
            if "options" in tag:
                for item in tag.options[0]:
                    options[item.key] = item.value

            self.tag_name = tag.name
            self.tag_options = options
            
        def parse_classname(item):
            self.classnames.append(item.name)

        def set_tagname_section(x):
            self.tag_name = "section"

        g_header.setParseAction(set_tagname_section)
        g_tag.setParseAction(parse_tag)
        g_classname.setParseAction(parse_classname)

        try:
            res = grammar.parseString(line)
        except pyp.ParseException as Ex:
            print 'Failed parsing "{}"'.format(line)
            raise Ex

        self.text = res['text'].strip()

        # If classnames are used but tag is None, default to a div
        if self.tag_name is None and self.classnames:
            self.tag_name = 'div'

        # Otherwise set the tag to text
        elif self.tag_name is None:
            self.tag_name = 'text'

        assert(self.tag_name is not None)

    @property
    def indent(self):
        is_space = lambda x:x in ['\t',' ']
        return len(list(itertools.takewhile(is_space,self.line)))

    @property
    def is_empty(self):
        if self.text or self.classnames:
            return False
        if self.tag_name != 'text':
            return False
        return True

    def __repr__(self):
        keys = ("text","tag_name","tag_options")
        vals = (getattr(self,x) for x in keys)
        return str(dict(zip(keys,vals)))

    def build_tag(self, soup, **kwargs):

        name = self.tag_name
        if name in _registered_custom_tags:
            tag = _registered_custom_tags[name](self, soup)
            
        else:
            tag = soup.new_tag(name)
        
        if self.classnames:
            tag['class'] = tag.get('class',[]) + self.classnames

        for key,val in self.tag_options.items():
            tag[key] = val

        for key,val in kwargs.items():
            tag[key] = val

        if self.text:
            # Make any markdown modifications
            text = inline_markdown_parser(self.text)
            html_text = bs4.BeautifulSoup(text,'html.parser')
            tag.append(html_text)

        return tag


class section(object):

    def __init__(self, lines):

        self.lines = []

        # Custom work for a code block
        is_inside_code_block = False
        code_buffer = []
        code_block_indent = None
        for line in lines:

            is_code_block = _code_block_marker == line.lstrip()[:3]

            if is_code_block:
                is_inside_code_block = not is_inside_code_block

            if is_code_block or is_inside_code_block:
                code_buffer.append( line.rstrip() )
    
            if is_code_block and not is_inside_code_block:
                space_ITR = itertools.takewhile(lambda x:x==' ',line)
                code_block_indent = len(list(space_ITR))

                # Remove the code buffer lines
                code_buffer = code_buffer[1:-1]
                
                # Empty out the contents of the buffer
                code_block = '__CODE_BLOCK_SPACE'.join(code_buffer)
                header = code_block_indent*' ' + '@codeblock '
                block = header + code_block
                self.lines.append(block)
                
                code_buffer = []
            elif not is_inside_code_block:
                self.lines.append(line)

    
        # Parse and filter for blank lines
        self.lines = [x for x in map(tagline, self.lines) if not x.is_empty]

        # Section shouldn't be empty
        assert(self.lines)

        # Section should start with a header
        assert(self.lines[0].tag_name == "section")
        
        soup  = bs4.BeautifulSoup("",'html.parser')
        lines = iter(self)
        
        # Parse the header
        z = lines.next().build_tag(soup, indent=-5)
        soup.append(z)
        
        for x in lines:

            tag = x.build_tag(soup, indent=x.indent)

            if x.tag_name in ["background", "background_video"]:
                assert(z.name == "section")
                z.append(tag)
                tag = soup.new_tag("div",indent=-2)
                tag["class"] = ["wrap",]
                z.append(tag)

            elif x.tag_name == "footer":
                z.findParent('section').append(tag)
            
            elif x.indent > z["indent"]:
                z.append(tag)
            
            elif x.indent == z["indent"]:
                z.parent.append(tag)

            elif x.indent < z["indent"]:
                
                while x.indent < z["indent"]:
                    z = z.parent

                # Take one more step so we are on the parent
                z.parent.append(tag)
                
            z = tag

        # We need to resoup the pot
        soup = bs4.BeautifulSoup(unicode(soup),'html.parser')
    
        # Remove all the indent tags
        for tag in soup.find_all(True, indent=True):
            del tag.attrs["indent"]

        # Remove all the text tags and replace with a string
        #for tag in soup.find_all("text"):
        #    tag.unwrap()

        self.soup = soup

    def __iter__(self):
        for line in self.lines:
            yield line

    def __repr__(self):
        return self.soup.prettify()

            
########################################################################

if __name__ == "__main__":

    section_text = '''----
@h1 (src='money' cash='true') .text-data
    @h2 .bg-red
        work it girl

@h1 .text-data / @h2 .bg-red
    work it girl
    '''.strip()

    S = section(section_text.split('\n'))
    for line in S:
        print line
