import sublime
import sublime_plugin
import datetime
import re
from pathlib import Path
import os
import fnmatch
from .orgparse.__init__ import *
import OrgExtended.orgparse.node as node
from   OrgExtended.orgparse.sublimenode import * 
import OrgExtended.orgutil.util as util
import OrgExtended.orgutil.navigation as nav
import OrgExtended.orgutil.template as templateEngine
import logging
import sys
import traceback 
import OrgExtended.orgfolding as folding
import OrgExtended.orgdb as db
import OrgExtended.asettings as sets
import OrgExtended.orgcapture as capture
import OrgExtended.orgextension as ext
import subprocess
import glob
import datetime
from collections import defaultdict, deque
import threading
import io
from subprocess import Popen, PIPE
import struct
import imghdr
import unicodedata
from collections import Counter
from operator import itemgetter
import base64
import urllib.request
import yaml

try:
    import importlib
except ImportError:
    pass

log = logging.getLogger(__name__)

# This is entirely copied from the wonderful OrgMode plugin
# found on package control. The orgmode plugin has a great base
# resolver system. I have copied it and will be extending it
# somewhat. OrgExtended is not compatible with orgmode
# so I have had to consume it here rather than just recommend
# you use it.


def find_all_links(view):
    links = view.find_by_selector("orgmode.link")
    return links

def extract_link(view):
    pt    = view.sel()[0].begin()
    links = find_all_links(view)
    for link in links:
        if(link.contains(pt)):
            return link
    return None


DEFAULT_LINK_RESOLVERS = [
    'http',
    'https',
    'prompt',
    'jira',
    'email',
    'file',
]

available_resolvers = ext.find_extension_modules('resolver')
linkre              = re.compile(r"\[\[([^\[\]]+)\]\s*(\[[^\[\]]*\])?\]")

# Returns the url from the full link
def extract_link_url(str):
    m = linkre.search(str)
    return m.group(1)

def extract_link_url_from_region(view, region):
    return extract_link_url(view.substr(region))

def is_region_link(view, region):
    return 'orgmode.link' in view.scope_name(region.end())

def get_link_region_at(view):
    if(is_region_link(view, view.sel()[0])):
        return extract_link(view)
    return None

def find_image_file(view, url):
    # ABS
    if(os.path.isabs(url)):
        return url
    # Relative
    if(view != None):
        curDir = os.path.dirname(view.file_name())
        filename = os.path.join(curDir, url)
        if(os.path.isfile(filename)):
            return filename
    # In search path
    searchHere = sets.Get("imageSearchPath",[])
    for direc in searchHere:
        filename = os.path.join(direc, url)
        if(os.path.isfile(filename)):
            return filename

    searchHere = sets.Get("orgDirs",[])
    for direc in searchHere:
        filename = os.path.join(direc, "images", url) 
        if(os.path.isfile(filename)):
            return filename

class OrgOpenLinkCommand(sublime_plugin.TextCommand):
    def resolve(self, content):
        for resolver in self.resolvers:
            result = resolver.resolve(content)
            if result is not None:
                return resolver, result
        return None, None

    def is_valid_scope(self, region):
        return is_region_link(self.view, region)

    def extract_content(self, region):
        return extract_link_url_from_region(self.view, region)

    def run(self, edit):
        # reload our resolvers if they are not loaded.
        #if(not hasattr(self, "resolvers")):
        wanted_resolvers = sets.Get("linkResolvers", DEFAULT_LINK_RESOLVERS)
        self.resolvers   = [available_resolvers[name].Resolver(self.view) for name in wanted_resolvers]

        view = self.view
        for sel in view.sel():
            if not self.is_valid_scope(sel):
                continue
            region = extract_link(view) #view.extract_scope(sel.end())
            content = self.extract_content(region)
            resolver, content = self.resolve(content)
            if content is None:
                sublime.error_message('Could not resolve link:\n%s' % content)
                continue
            resolver.execute(content)

# global magic
VIEWS_WITH_IMAGES = set()

# Stolen from:
# https://github.com/renerocksai/sublime_zk/blob/master/sublime_zk.py
# The excellent work in that system showed a way of using
# Phantoms to show images inline. This gives us a part of one of Org Modes
# Most powerful features which is babel modes ability to make diagrams in
# documents.
class ImageHandler:
    Phantoms = defaultdict(set)
    Cache    = {}

    @staticmethod
    def save_cache():
       user_settings_path = os.path.join(sublime.packages_path(), "User","orgextended_image_cache.yaml")
       f = open(user_settings_path,"w")
       data = yaml.dump(ImageHandler.Cache, f)
       f.close() 

    @staticmethod
    def load_cache():
        user_settings_path = os.path.join(sublime.packages_path(), "User","orgextended_image_cache.yaml")
        if(os.path.isfile(user_settings_path)):
            stream = open(user_settings_path, 'r')
            ImageHandler.Cache = yaml.load(stream, Loader=yaml.SafeLoader)
            stream.close()

    @staticmethod
    def show_image(region, view, max_width=1024):
        # If we already have this image then exit out
        if view.id() in ImageHandler.Phantoms and str(region) in ImageHandler.Phantoms[view.id()]:
            return
        url    = extract_link_url_from_region(view, region)
        # We can only handle links to images this way.
        if not util.is_image(url):
            return
        level  = db.Get().GetIndentForRegion(view, region)
        indent = "&nbsp;" * (level * 2)
        if url.startswith('http') or url.startswith('https'):
            img = url
            local_filename = None
            if(url in ImageHandler.Cache and os.path.isfile(ImageHandler.Cache[url])):
                local_filename = ImageHandler.Cache[url]
                log.debug("Loaded from cache: " + url)
            else:
                log.debug("Downloaded: " + url)
                local_filename, headers = urllib.request.urlretrieve(url)
                ImageHandler.Cache[url] = local_filename
                ImageHandler.save_cache()
            size = ImageHandler.get_image_size(local_filename)
            if size:
                w, h, ttype = size
                FMT = u'''
                    {}<img src="data:image/{}" class="centerImage" {}>
                '''
            img = ttype + ";base64," + util.get_as_base64(img)
        elif url.startswith("file:"):
            url = url.replace("file:","")
            log.debug("FILE: " + url)
            FMT = '''
                {}<img src="file://{}" class="centerImage" {}>
            '''
            img  = find_image_file(view, url)
            size = ImageHandler.get_image_size(img)
        else:
            log.debug("local file: " + url)
            FMT = '''
                {}<img src="file://{}" class="centerImage" {}>
            '''
            img  = find_image_file(view, url)
            log.debug("local file2: " + url)
            size = ImageHandler.get_image_size(img)
        if not size:
            return
        w, h, t = size
        line_region = view.line(region)
        imgattr = ImageHandler.check_imgattr(view, line_region, region)
        if not imgattr:
            if w > max_width:
                m = max_width / w
                h *= m
                w = max_width
            imgattr = 'width="{}" height="{}"'.format(w, h)

        view.erase_phantoms(str(region))
        html_img = FMT.format(indent, img, imgattr)
        view.add_phantom(str(region), region, html_img, sublime.LAYOUT_BLOCK)
        ImageHandler.Phantoms[view.id()].add(str(region))

    @staticmethod
    def hide_image(region, view):
        view.erase_phantoms(str(region))
        ImageHandler.Phantoms[view.id()].remove(str(region))

    @staticmethod
    def show_image_at(view, max_width=1024):
        reg = get_link_region_at(view)
        if(reg):
            ImageHandler.show_image(reg, view)

    @staticmethod
    def hide_image_at(view, max_width=1024):
        reg = get_link_region_at(view)
        if(reg):
            ImageHandler.hide_image(reg, view)

    @staticmethod
    def show_images(view, max_width=1024):
        global VIEWS_WITH_IMAGES
        skip = 0

        while True:
            imageRegions = view.find_by_selector('orgmode.link')[skip:]
            skip += 1
            if not imageRegions:
                break
            region = imageRegions[0]
            ImageHandler.show_image(region, view, max_width)
        VIEWS_WITH_IMAGES.add(view.id())

    @staticmethod
    def check_imgattr(view, line_region, link_region=None):
        # find attrs for this link
        full_line = view.substr(line_region)
        link_till_eol = full_line[link_region.a - line_region.a:]
        # find attr if present
        m = re.match(r'.*\)\{(.*)\}', link_till_eol)
        if m:
            return m.groups()[0]

    @staticmethod
    def hide_images(view, edit):
        for rel_p in ImageHandler.Phantoms[view.id()]:
            view.erase_phantoms(rel_p)
        del ImageHandler.Phantoms[view.id()]
        skip = 0
        while True:
            img_regs = view.find_by_selector('orgmode.link.href')[skip:]
            skip += 1
            if not img_regs:
                break
            region = img_regs[0]
            rel_p = view.substr(region)
            if(util.is_image(rel_p)):
                line_region = view.line(region)
                line_str = view.substr(line_region)
                view.replace(edit, line_region, line_str.strip())
        VIEWS_WITH_IMAGES.discard(view.id())

    @staticmethod
    def get_image_size(img):
        """
        Determine the image type of img and return its size.
        """
        with open(img, 'rb') as f:
            head = f.read(24)
            ttype = None

            # print('head:\n', repr(head))
            if len(head) != 24:
                return
            if imghdr.what(img) == 'png':
                ttype = "png"
                check = struct.unpack('>i', head[4:8])[0]
                if check != 0x0d0a1a0a:
                    return
                width, height = struct.unpack('>ii', head[16:24])
            elif imghdr.what(img) == 'gif':
                ttype = "gif"
                width, height = struct.unpack('<HH', head[6:10])
            elif imghdr.what(img) == 'jpeg':
                ttype = "jpeg"
                try:
                    f.seek(0)  # Read 0xff next
                    size = 2
                    ftype = 0
                    while not 0xc0 <= ftype <= 0xcf:
                        f.seek(size, 1)
                        byte = f.read(1)
                        while ord(byte) == 0xff:
                            byte = f.read(1)
                        ftype = ord(byte)
                        size = struct.unpack('>H', f.read(2))[0] - 2
                    # SOFn block
                    f.seek(1, 1)  # skip precision byte.
                    height, width = struct.unpack('>HH', f.read(4))
                except Exception:
                    return
            else:
                return
            return width, height, ttype


class OrgShowImagesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ImageHandler.show_images(self.view)

class OrgHideImagesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ImageHandler.hide_images(self.view, edit)

class OrgShowImageCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ImageHandler.show_image_at(self.view)

class OrgHideImageCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        ImageHandler.hide_image_at(self.view)

# ON LOAD HANDLER: if startup is set we show or hide the images.
#+STARTUP: inlineimages
#+STARTUP: noinlineimages
def get_show_images_default():
    return sets.Get("startup",["noinlineimages"])

def get_image_startup(node):
    startupDefault = get_show_images_default()
    return node.startup(startupDefault)

def onShutdown():
    ImageHandler.save_cache()

def onLoad(view):
    ImageHandler.load_cache()
    file = db.Get().FindInfo(view)
    if(file):
        startup = get_image_startup(file.org[0])
        if(Startup.inlineimages in startup):
            ImageHandler.show_images(view)