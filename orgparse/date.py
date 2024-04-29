import datetime
import re
import dateutil.rrule as dr
import dateutil.parser as dp
import dateutil.relativedelta as drel
import OrgExtended.orgduration as orgduration
import calendar

def total_seconds(td):
    """Equivalent to `datetime.timedelta.total_seconds`."""
    return float(td.microseconds +
                 (td.seconds + td.days * 24 * 3600) * 10 ** 6) / 10 ** 6


def total_minutes(td):
    """Alias for ``total_seconds(td) / 60``."""
    return total_seconds(td) / 60


def gene_timestamp_regex(brtype, prefix=None, nocookie=False):
    """
    Generate timetamp regex for active/inactive/nobrace brace type

    :type brtype: {'active', 'inactive', 'nobrace'}
    :arg  brtype:
        It specifies a type of brace.
        active: <>-type; inactive: []-type; nobrace: no braces.

    :type prefix: str or None
    :arg  prefix:
        It will be appended to the head of keys of the "groupdict".
        For example, if prefix is ``'active_'`` the groupdict has
        keys such as ``'active_year'``, ``'active_month'``, and so on.
        If it is None it will be set to ``brtype`` + ``'_'``.

    :type nocookie: bool
    :arg  nocookie:
        Cookie part (e.g., ``'-3d'`` or ``'+6m'``) is not included if
        it is ``True``.  Default value is ``False``.

    >>> timestamp_re = re.compile(
    ...     gene_timestamp_regex('active', prefix=''),
    ...     re.VERBOSE)
    >>> timestamp_re.match('no match')  # returns None
    >>> m = timestamp_re.match('<2010-06-21 Mon>')
    >>> m.group()
    '<2010-06-21 Mon>'
    >>> '{year}-{month}-{day}'.format(**m.groupdict())
    '2010-06-21'
    >>> m = timestamp_re.match('<2005-10-01 Sat 12:30 +7m -3d>')
    >>> from collections import OrderedDict
    >>> sorted(m.groupdict().items())
    ... # doctest: +NORMALIZE_WHITESPACE
    [('day', '01'),
     ('end_hour', None), ('end_min', None),
     ('hour', '12'), ('min', '30'),
     ('month', '10'),
     ('repeatdwmy', 'm'), ('repeatnum', '7'), ('repeatpre', '+'),
     ('warndwmy', 'd'), ('warnnum', '3'), ('warnpre', '-'), ('year', '2005')]

    When ``brtype = 'nobrace'``, cookie part cannot be retrieved.

    >>> timestamp_re = re.compile(
    ...     gene_timestamp_regex('nobrace', prefix=''),
    ...     re.VERBOSE)
    >>> timestamp_re.match('no match')  # returns None
    >>> m = timestamp_re.match('2010-06-21 Mon')
    >>> m.group()
    '2010-06-21'
    >>> '{year}-{month}-{day}'.format(**m.groupdict())
    '2010-06-21'
    >>> m = timestamp_re.match('2005-10-01 Sat 12:30 +7m -3d')
    >>> sorted(m.groupdict().items())
    ... # doctest: +NORMALIZE_WHITESPACE
    [('day', '01'),
     ('end_hour', None), ('end_min', None),
     ('hour', '12'), ('min', '30'),
     ('month', '10'), ('year', '2005')]
    """

    if brtype == 'active':
        (bo, bc) = ('<', '>')
    elif brtype == 'inactive':
        (bo, bc) = (r'\[', r'\]')
    elif brtype == 'nobrace':
        (bo, bc) = ('', '')
    else:
        raise ValueError("brtype='{0!r}' is invalid".format(brtype))

    if brtype == 'nobrace':
        ignore = r'[\s\w]'
    else:
        ignore = '[^{bc}]'.format(bc=bc)

    if prefix is None:
        prefix = '{0}_'.format(brtype)

    regex_date_time = r"""
        (?P<{prefix}year>\d{{4}}) -
        (?P<{prefix}month>\d{{2}}) -
        (?P<{prefix}day>\d{{2}})
        (  # optional time field
           ({ignore}+?)
           (?P<{prefix}hour>\d{{2}}) :
           (?P<{prefix}min>\d{{2}})
           (  # optional end time range
               --?
               (?P<{prefix}end_hour>\d{{2}}) :
               (?P<{prefix}end_min>\d{{2}})
           )?
        )?
        """
    regex_cookie = r"""
        (  # optional repeater
           ({ignore}+?)
           (?P<{prefix}repeatpre>  [\.\+]{{1,2}})
           (?P<{prefix}repeatnum>  \d+)
           (?P<{prefix}repeatdwmy> [dwmy])
        )?
        (  # optional warning
           ({ignore}+?)
           (?P<{prefix}warnpre>  \-)
           (?P<{prefix}warnnum>  \d+)
           (?P<{prefix}warndwmy> [dwmy])
        )?
        """
    # http://www.pythonregex.com/
    regex = ''.join([
        bo,
        regex_date_time,
        regex_cookie if nocookie or brtype != 'nobrace' else '',
        '({ignore}*?)',
        bc])
    return regex.format(prefix=prefix, ignore=ignore)


TIMESTAMP_NOBRACE_RE = re.compile(
    gene_timestamp_regex('nobrace', prefix=''),
    re.VERBOSE)

TIMESTAMP_RE = re.compile(
    '|'.join((gene_timestamp_regex('active'),
              gene_timestamp_regex('inactive'))),
    re.VERBOSE)
def copy_repeat_info(f,t):
    if(f and hasattr(f,'repeat_rule') and f.repeat_rule):
        t.repeatpre = f.repeatpre
        t.repeatdwmy  = f.repeatdwmy
        t.repeat_rule = f.repeat_rule
        t.freq = f.freq


def get_repeat_info(rv,mdict):

    for prefix in ['active_','inactive_']:
        if(prefix+'repeatpre' in mdict):
            repeatpre  = mdict[prefix+'repeatpre']
            repeatnum  = mdict[prefix+'repeatnum']
            repeatdwmy = mdict[prefix+'repeatdwmy']
            if(repeatdwmy is not None and repeatpre is not None):
                rv.freq = dr.DAILY
                if(repeatdwmy == 'y'):
                    rv.freq = dr.YEARLY
                if(repeatdwmy == 'm'):
                    rv.freq = dr.MONTHLY
                if(repeatdwmy == 'w'):
                    rv.freq = dr.WEEKLY
                rv.repeatnum = int(repeatnum)
                if(rv.repeatnum <= 0):
                    rv.repeatnum = 1
                # Build an org mode repeat rule
                rv.repeat_rule = dr.rrule(rv.freq,interval=rv.repeatnum,dtstart=rv.start,cache=True) 
                # This determines what to do when you mark the task as done.
                # + just bump to the next FIXED interval (even if thats in the past)
                # ++ bump to the next FIXED interval, in the future. (IE next sunday) even if you missed some.
                # .+ bump but change the start date to today. 
                rv.repeatpre   = repeatpre
                rv.repeatdwmy  = repeatdwmy
        if(prefix+'warnpre' in mdict):
            warnpre  = mdict[prefix+'warnpre']
            warnnum  = mdict[prefix+'warnnum']
            warndwmy = mdict[prefix+'warndwmy']
            if(warndwmy is not None and warnpre is not None):
                rv.warnnum = int(warnnum)
                if(rv.warnnum <= 0):
                    rv.warnnum = 1
                rv.wfreq = dr.DAILY
                rv.warn_rule = datetime.timedelta(days=rv.warnnum)
                if(warndwmy == 'y'):
                    rv.warn_rule = datetime.timedelta(years=rv.warnnum)
                    rv.wfreq = dr.YEARLY
                if(warndwmy == 'm'):
                    rv.warn_rule = datetime.timedelta(months=rv.warnnum)
                    rv.wfreq = dr.MONTHLY
                if(warndwmy == 'w'):
                    rv.warn_rule = datetime.timedelta(weeks=rv.warnnum)
                    rv.wfreq = dr.WEEKLY
                rv.warnpre   = warnpre
                rv.warndwmy  = warndwmy

class OrgDate(object):

    _active_default = True
    """
    The default active value.

    When the `active` argument to ``__init__`` is ``None``,
    This value will be used.

    """

    def __init__(self, start, end=None, active=None, repeat_rule=None, warn_rule=None):
        """
        Create :class:`OrgDate` object

        :type start: datetime, date, tuple, int, float or None
        :type   end: datetime, date, tuple, int, float or None
        :arg  start: Starting date.
        :arg    end: Ending date.

        :type active: bool or None
        :arg  active: Active/inactive flag.
                      None means using its default value, which
                      may be different for different subclasses.

        >>> OrgDate(datetime.date(2012, 2, 10))
        OrgDate((2012, 2, 10))
        >>> OrgDate((2012, 2, 10))
        OrgDate((2012, 2, 10))
        >>> OrgDate((2012, 2))  #doctest: +NORMALIZE_WHITESPACE
        Traceback (most recent call last):
            ...
        ValueError: Automatic conversion to the datetime object
        requires at least 3 elements in the tuple.
        Only 2 elements are in the given tuple '(2012, 2)'.
        >>> OrgDate((2012, 2, 10, 12, 20, 30))
        OrgDate((2012, 2, 10, 12, 20, 30))
        >>> OrgDate((2012, 2, 10), (2012, 2, 15), active=False)
        OrgDate((2012, 2, 10), (2012, 2, 15), False)

        OrgDate can be created using unix timestamp:

        >>> OrgDate(datetime.datetime.fromtimestamp(0)) == OrgDate(0)
        True

        """
        self._start = self._to_date(start)
        self._end = self._to_date(end)
        self._active = self._active_default if active is None else active
        self.repeat_rule = repeat_rule
        self.warn_rule = warn_rule

    def __add__(self,o):
        if(isinstance(o,int)):
            d = orgduration.OrgDuration.ParseInt(o)
            td = d.timedelta()
            return OrgDate(self._start + td,self._end + td if self._end else None,self._active,self.repeat_rule,self.warn_rule)
        if(isinstance(o,orgduration.OrgDuration)):
            td = o.timedelta()
            return OrgDate(self._start + td,self._end + td if self._end else None,self._active,self.repeat_rule,self.warn_rule)
        return self 
        pass
    
    def __sub__(self,o):
        if(isinstance(o,int)):
            d = orgduration.OrgDuration.ParseInt(o)
            td = d.timedelta()
            return OrgDate(self._start - td,self._end - td if self._end else None,self._active,self.repeat_rule,self.warn_rule)
        if(isinstance(o,orgduration.OrgDuration)):
            td = o.timedelta()
            return OrgDate(self._start - td,self._end - td if self._end else None,self._active,self.repeat_rule,self.warn_rule)
        return self 

    def before_duration(self, duration):
        return self._start <= (datetime.datetime.now() + duration.timedelta())
    
    def after_duration(self, duration):
        end = self._end if self._end is not None else self._start
        return end >= (datetime.datetime.now() - duration.timedelta())

    @staticmethod
    def format_date(now, active):
        if(active):
            return now.strftime("<%Y-%m-%d %a>")  
        else:
            return now.strftime("[%Y-%m-%d %a]")  

    @staticmethod
    def format_clock(now, active):
        if(active):
            return now.strftime("<%Y-%m-%d %a %H:%M>")  
        else:
            return now.strftime("[%Y-%m-%d %a %H:%M]")  
    
    @staticmethod
    def format_clock_with_time_range(s, e, active):
        if(active):
            return s.strftime("<%Y-%m-%d %a %H:%M-") + e.strftime("%H:%M>")
        else:
            return s.strftime("[%Y-%m-%d %a %H:%M-") + e.strftime("%H:%M>")

    @staticmethod
    def format_datetime(now):
        if(isinstance(now,datetime.datetime)):
            return now.strftime("%Y-%m-%d %a %H:%M")  
        else:
            return now.strftime("%Y-%m-%d %a")  
    
    @staticmethod
    def format_duration(d):
        hours   = d.seconds/3600
        minutes = (d.seconds/60)%60 
        return "{0:02d}:{1:02d}".format(int(hours),int(minutes))    

    @staticmethod
    def format_dwim(start, end=None,active=False):
        if(end):
            if(isinstance(start, datetime.datetime)):
                if(end.date() != start.date()):
                    duration = end - start
                    return "{0}--{1} => {2}".format(
                        OrgDate.format_clock(start, active), 
                        OrgDate.format_clock(end, active), 
                        OrgDate.format_duration(duration))
                else:
                    return OrgDate.format_clock_with_time_range(start,end,active)
            else:
                if(end == start):
                    return OrgDate.format_date(start,active)
                else:
                    duration = end - start
                    return "{0}--{1} => {2}".format(
                        OrgDate.format_date(start, active), 
                        OrgDate.format_date(end, active), 
                        OrgDate.format_duration(duration))
        else:
            if(isinstance(start, datetime.datetime)):
                return OrgDate.format_clock(start, active)
            else:
                return OrgDate.format_date(start,active)

    @staticmethod
    def format_as_clock(start, end=None,active=False):
        if(end):
            duration = end - start
            return "{0}--{1} => {2}".format(
                OrgDate.format_clock(start, active), 
                OrgDate.format_clock(end, active), 
                OrgDate.format_duration(duration))
        else:
            return "{0}--".format(
                OrgDate.format_clock(start, active))

    def format_clock_str(self):
        return OrgDate.format_as_clock(self._start, self._end)
    
    def format_datetime_str(self):
        return OrgDate.format_datetime(self._start)

    @staticmethod
    def _to_date(date):
        if isinstance(date, (tuple, list)):
            if len(date) == 3:
                return datetime.date(*date)
            elif len(date) > 3:
                return datetime.datetime(*date)
            else:
                raise ValueError(
                    "Automatic conversion to the datetime object "
                    "requires at least 3 elements in the tuple. "
                    "Only {0} elements are in the given tuple '{1}'."
                    .format(len(date), date))
        elif isinstance(date, (int, float)):
            return datetime.datetime.fromtimestamp(date)
        else:
            return date

    @staticmethod
    def _date_to_tuple(date):
        if isinstance(date, datetime.datetime):
            return tuple(date.timetuple()[:6])
        elif isinstance(date, datetime.date):
            return tuple(date.timetuple()[:3])

    def __str__(self):
        # TODO: Handle recurrence in this!
        return self.format_dwim(self._start, self._end, self._active)

    def __repr__(self):
        args = [
            self.__class__.__name__,
            self._date_to_tuple(self.start),
            self._date_to_tuple(self.end) if self.has_end() else None,
            None if self._active is self._active_default else self._active,
        ]
        if args[2] is None and args[3] is None:
            return '{0}({1!r})'.format(*args)
        elif args[3] is None:
            return '{0}({1!r}, {2!r})'.format(*args)
        else:
            return '{0}({1!r}, {2!r}, {3!r})'.format(*args)

    def __nonzero__(self):
        return bool(self._start)

    __bool__ = __nonzero__  # PY3

    def __eq__(self, other):
        if (isinstance(other, OrgDate) and
            self._start is None and
            other._start is None):
            return True
        return (isinstance(other, self.__class__) and
                self._start == other._start and
                self._end == other._end and
                self._active == other._active)

    @property
    def repeating(self):
        return self.repeat_rule != None

    @property
    def warning(self):
        return self.warn_rule != None

    @property
    def next_repeat_from_now(self):
        now = datetime.datetime.now()
        return self.repeat_rule.after(now,inc=True) 

    @property
    def next_repeat_from_today(self):
        # NOTE: This will be on midnight if the schedule doesn't have a time
        now = datetime.datetime.now()
        now = now.replace(hour=0,minute=0,second=0,microsecond=0)
        return self.repeat_rule.after(now,inc=True) 

    def next_repeat_from(self,now):
        #now = now.replace(hour=0,minute=0,second=0,microsecond=0)
        return self.repeat_rule.after(now,inc=False) 

    @property
    def start(self):
        """
        Get date or datetime object

        >>> OrgDate((2012, 2, 10)).start
        datetime.date(2012, 2, 10)
        >>> OrgDate((2012, 2, 10, 12, 10)).start
        datetime.datetime(2012, 2, 10, 12, 10)

        """
        return self._start

    @property
    def end(self):
        """
        Get date or datetime object

        >>> OrgDate((2012, 2, 10), (2012, 2, 15)).end
        datetime.date(2012, 2, 15)
        >>> OrgDate((2012, 2, 10, 12, 10), (2012, 2, 15, 12, 10)).end
        datetime.datetime(2012, 2, 15, 12, 10)

        """
        return self._end

    def is_active(self):
        """Return true if the date is active"""
        return self._active

    def has_end(self):
        """Return true if it has the end date"""
        return bool(self._end)

    def has_time(self):
        """
        Return true if the start date has time field

        >>> OrgDate((2012, 2, 10)).has_time()
        False
        >>> OrgDate((2012, 2, 10, 12, 10)).has_time()
        True

        """
        return isinstance(self._start, datetime.datetime)

    def has_overlap(self, other):
        """
        Test if it has overlap with other :class:`OrgDate` instance

        If the argument is not an instance of :class:`OrgDate`, it is
        converted to :class:`OrgDate` instance by ``OrgDate(other)``
        first.

        >>> od = OrgDate((2012, 2, 10), (2012, 2, 15))
        >>> od.has_overlap(OrgDate((2012, 2, 11)))
        True
        >>> od.has_overlap(OrgDate((2012, 2, 20)))
        False
        >>> od.has_overlap(OrgDate((2012, 2, 11), (2012, 2, 20)))
        True
        >>> od.has_overlap((2012, 2, 11))
        True

        """
        if not isinstance(other, OrgDate):
            other = OrgDate(other)
        if self.has_end():
            return self._datetime_in_range(other.start) or self._datetime_in_range(other.end)
        elif other.has_end():
            return other._datetime_in_range(self.start)
        else:
            # These could be datetime entries
            # do we care about the hours, probably not!
            # this is containement and we are just a point
            # if these are on the same day we are okay.
            ss = self.start
            os = other.start
            if(not type(ss) == datetime.date):
                ss = ss.date()
            if(not type(os) == datetime.date):
                os = os.date()
            return ss == os

    def after(self, date): 
        if not isinstance(date, (datetime.datetime, datetime.date)):
            return False
        if isinstance(date, datetime.datetime) and isinstance(self.start, datetime.datetime):
            return self.start > date
        return _as_date(self.start) > _as_date(date)

    def before(self, date): 
        if not isinstance(date, (datetime.datetime, datetime.date)):
            return False
        self_date = self._end if self.has_end() else self._start
        if isinstance(date, datetime.datetime) and isinstance(self_date, datetime.datetime):
            return self_date < date
        return _as_date(self_date) < _as_date(date)

    def _datetime_in_range(self, date):
        if not isinstance(date, (datetime.datetime, datetime.date)):
            return False
        return not self.before(date) and not self.after(date)

    @staticmethod
    def _daterange_from_groupdict(dct, prefix=''):
        start_keys = ['year', 'month', 'day', 'hour'    , 'min']
        end_keys   = ['year', 'month', 'day', 'end_hour', 'end_min']
        start_range = list(map(int, filter(None, (dct[prefix + k] for k in start_keys))))
        end_range   = list(map(int, filter(None, (dct[prefix + k] for k in end_keys))))
        if len(end_range) < len(end_keys):
            end_range = None
        return (start_range, end_range)

    def add_days(self, inc):
        self._start += datetime.timedelta(days=inc)
        if(self._end):
            self._end += datetime.timedelta(days=inc)
        # TODO: Handle recurrence rules

    def add_hours(self, inc):
        self._start += datetime.timedelta(hours=inc)
        if(self._end):
            self._end += datetime.timedelta(hours=inc)
    
    def add_minutes(self, inc):
        self._start += datetime.timedelta(minutes=inc)
        if(self._end):
            self._end += datetime.timedelta(minutes=inc)

    @staticmethod
    def date_add_months(sourcedate,months):
        month = sourcedate.month - 1 + months
        year = sourcedate.year + month // 12
        month = month % 12 + 1
        day = min(sourcedate.day,calendar.monthrange(year,month)[1])
        return datetime.date(year,month,day)

    def add_months(self, inc):
        forward = True
        if inc < 0:
            inc = -inc
            forward = False
        for i in range(0,inc):
            if(forward):
                smonth = self._start.month
                syear  = self._start.year
                self._start += datetime.timedelta(days=calendar.monthrange(syear,smonth)[1])
                if(self._end):
                    emonth = self._end.month
                    eyear  = self._end.year
                    self._end += datetime.timedelta(days=calendar.monthrange(eyear,emonth)[1])
            else:
                # TODO: This does not work
                # This should be last months day count.
                smonth = self._start.month - 1
                syear  = self._start.year
                if(smonth <= 0):
                    smonth = 12
                    syear -= 1
                self._start -= datetime.timedelta(days=(calendar.monthrange(syear,smonth)[1]))
                if(self._end):
                    emonth = self._end.month - 1
                    eyear  = self._end.year
                    if(smonth <= 0):
                        send = 12
                        send -= 1
                    self._end -= datetime.timedelta(days=(calendar.monthrange(eyear,emonth)[1]))
        # TODO: Handle recurrence rules

    @classmethod
    def _datetuple_from_groupdict(cls, dct, prefix=''):
        return cls._daterange_from_groupdict(dct, prefix=prefix)[0]


    @classmethod
    def list_from_str(cls, string):
        """
        Parse string and return a list of :class:`OrgDate` objects

        >>> OrgDate.list_from_str("... <2012-02-10 Fri> and <2012-02-12 Sun>")
        [OrgDate((2012, 2, 10)), OrgDate((2012, 2, 12))]
        >>> OrgDate.list_from_str("<2012-02-10 Fri>--<2012-02-12 Sun>")
        [OrgDate((2012, 2, 10), (2012, 2, 12))]
        >>> OrgDate.list_from_str("<2012-02-10 Fri>--[2012-02-12 Sun]")
        [OrgDate((2012, 2, 10)), OrgDate((2012, 2, 12), None, False)]
        >>> OrgDate.list_from_str("this is not timestamp")
        []
        >>> OrgDate.list_from_str("<2012-02-11 Sat 10:11--11:20>")
        [OrgDate((2012, 2, 11, 10, 11, 0), (2012, 2, 11, 11, 20, 0))]
        """
        match = TIMESTAMP_RE.search(string)
        if match:
            rest = string[match.end():]
            mdict = match.groupdict()
            if mdict['active_year']:
                prefix = 'active_'
                active = True
                rangedash = '--<'
            else:
                prefix = 'inactive_'
                active = False
                rangedash = '--['
            has_rangedash = rest.startswith(rangedash)
            match2 = TIMESTAMP_RE.search(rest) if has_rangedash else None
            if has_rangedash and match2:
                rest = rest[match2.end():]
                # no need for check activeness here because of the rangedash
                mdict2 = match2.groupdict()
                odate = cls(
                    cls._datetuple_from_groupdict(mdict, prefix),
                    cls._datetuple_from_groupdict(mdict2, prefix),
                    active=active)
            else:
                odate = cls(
                    *cls._daterange_from_groupdict(mdict, prefix),
                    active=active)
            get_repeat_info(odate, mdict)
            # FIXME: treat "repeater" and "warn"
            ndate = cls.list_from_str(rest)
            if len(ndate) > 0:
                copy_repeat_info(ndate[0], odate)
            return [odate] + ndate
        else:
            return []



    @classmethod
    def from_str(cls, string):
        """
        Parse string and return an :class:`OrgDate` objects.

        >>> OrgDate.from_str('2012-02-10 Fri')
        OrgDate((2012, 2, 10))
        >>> OrgDate.from_str('2012-02-10 Fri 12:05')
        OrgDate((2012, 2, 10, 12, 5, 0))

        """
        match = cls._from_str_re.match(string)
        if match:
            mdict = match.groupdict()
            return cls(cls._datetuple_from_groupdict(mdict),
                       active=cls._active_default)
        else:
            return cls(None)

    _from_str_re = TIMESTAMP_NOBRACE_RE

def _as_date(date_or_datetime):
    if isinstance(date_or_datetime, datetime.datetime):
        return date_or_datetime.date()
    return date_or_datetime

def _as_datetime(date_or_datetime):
    if isinstance(date_or_datetime, datetime.datetime):
        return date_or_datetime
    return datetime.datetime(*date_or_datetime.timetuple()[:3])

def compile_sdc_re(sdctype):
    brtype = 'inactive' if sdctype == 'CLOSED' else 'active'
    return re.compile(
        r'^(?!\#).*{0}:\s+{1}'.format(
            sdctype,
            gene_timestamp_regex(brtype, prefix='', nocookie=True)),
        re.VERBOSE | re.MULTILINE)


class OrgDateSDCBase(OrgDate):

    _re = None  # override this!

    @classmethod
    def search(cls, string):
        return cls._re.search(string)

    # FIXME: use OrgDate.from_str
    @classmethod
    def from_str(cls, string):
        match = cls.search(string)
        if match:
            mdict = match.groupdict()
            start = cls._datetuple_from_groupdict(mdict)
            end = None
            end_hour = mdict['end_hour']
            end_min  = mdict['end_min']
            if end_hour is not None and end_min is not None:
                end_dict = {}
                end_dict.update(mdict)
                end_dict.update({'hour': end_hour, 'min': end_min})
                end = cls._datetuple_from_groupdict(end_dict)
            rv = cls(start, end, active=cls._active_default)

            repeatpre  = mdict['repeatpre']
            repeatnum  = mdict['repeatnum']
            repeatdwmy = mdict['repeatdwmy']
            if(repeatdwmy is not None and repeatpre is not None):
                rv.freq = dr.DAILY
                if(repeatdwmy == 'y'):
                    rv.freq = dr.YEARLY
                if(repeatdwmy == 'm'):
                    rv.freq = dr.MONTHLY
                if(repeatdwmy == 'w'):
                    rv.freq = dr.WEEKLY
                rv.repeatnum = int(repeatnum)
                if(rv.repeatnum <= 0):
                    rv.repeatnum = 1
                # Build an org mode repeat rule
                rv.repeat_rule = dr.rrule(rv.freq,interval=rv.repeatnum,dtstart=rv.start,cache=True) 
                # This determines what to do when you mark the task as done.
                # + just bump to the next FIXED interval (even if thats in the past)
                # ++ bump to the next FIXED interval, in the future. (IE next sunday) even if you missed some.
                # .+ bump but change the start date to today. 
                rv.repeatpre   = repeatpre
                rv.repeatdwmy  = repeatdwmy

            warnpre  = mdict['warnpre']
            warnnum  = mdict['warnnum']
            warndwmy = mdict['warndwmy']
            if(warndwmy is not None and warnpre is not None):
                rv.warnnum = int(warnnum)
                if(rv.warnnum <= 0):
                    rv.warnnum = 1
                rv.wfreq = dr.DAILY
                rv.warn_rule = datetime.timedelta(days=rv.warnnum)
                if(warndwmy == 'y'):
                    rv.warn_rule = datetime.timedelta(years=rv.warnnum)
                    rv.wfreq = dr.YEARLY
                if(warndwmy == 'm'):
                    rv.warn_rule = datetime.timedelta(months=rv.warnnum)
                    rv.wfreq = dr.MONTHLY
                if(warndwmy == 'w'):
                    rv.warn_rule = datetime.timedelta(weeks=rv.warnnum)
                    rv.wfreq = dr.WEEKLY
                rv.warnpre   = warnpre
                rv.warndwmy  = warndwmy
            return rv
        else:
            return cls(None)


class OrgDateScheduled(OrgDateSDCBase):
    """Date object to represent SCHEDULED attribute."""
    _re = compile_sdc_re('SCHEDULED')
    _active_default = True


class OrgDateDeadline(OrgDateSDCBase):
    """Date object to represent DEADLINE attribute."""
    _re = compile_sdc_re('DEADLINE')
    _active_default = True


class OrgDateClosed(OrgDateSDCBase):
    """Date object to represent CLOSED attribute."""
    _re = compile_sdc_re('CLOSED')
    _active_default = False

def compile_nsdc_re():
    brtype = 'nobrace' 
    return re.compile(r'^\s*{0}'.format(gene_timestamp_regex(brtype, prefix='', nocookie=True)),re.VERBOSE)

class OrgDateFreeFloating(OrgDateSDCBase):
    _active_default = False
    _re = compile_nsdc_re()



def parse_sdc(string):
    return (OrgDateScheduled.from_str(string),
            OrgDateDeadline.from_str(string),
            OrgDateClosed.from_str(string))


class OrgDateClock(OrgDate):

    """
    Date object to represent CLOCK attributes.

    >>> OrgDateClock.from_str(
    ...   'CLOCK: [2010-08-08 Sun 17:00]--[2010-08-08 Sun 17:30] =>  0:30')
    OrgDateClock((2010, 8, 8, 17, 0, 0), (2010, 8, 8, 17, 30, 0))

    """

    _active_default = False

    def __init__(self, start, end, duration=None, active=None):
        """
        Create OrgDateClock object
        """
        super(OrgDateClock, self).__init__(start, end, active=active)
        self._duration = duration

    @property
    def duration(self):
        """
        Get duration of CLOCK.

        >>> duration = OrgDateClock.from_str(
        ...   'CLOCK: [2010-08-08 Sun 17:00]--[2010-08-08 Sun 17:30] => 0:30'
        ... ).duration
        >>> duration.seconds
        1800
        >>> total_minutes(duration)
        30.0

        """
        return self.end - self.start

    def is_duration_consistent(self):
        """
        Check duration value of CLOCK line.

        >>> OrgDateClock.from_str(
        ...   'CLOCK: [2010-08-08 Sun 17:00]--[2010-08-08 Sun 17:30] => 0:30'
        ... ).is_duration_consistent()
        True
        >>> OrgDateClock.from_str(
        ...   'CLOCK: [2010-08-08 Sun 17:00]--[2010-08-08 Sun 17:30] => 0:15'
        ... ).is_duration_consistent()
        False

        """
        return (self._duration is None or
                self._duration == total_minutes(self.duration))

    @classmethod
    def from_str(cls, line):
        """
        Get CLOCK from given string.

        Return three tuple (start, stop, length) which is datetime object
        of start time, datetime object of stop time and length in minute.

        """
        match = cls._re.search(line)
        if not match:
            return cls(None, None)
        d1 = None
        d2 = None
        dd = 0
        y = match.group('y1')
        if y:
            d1 = datetime.datetime(int(y), int(match.group('mo1')), int(match.group('d1')), int(match.group('h1')), int(match.group('m1')))
        y = match.group('y2')
        if y:
            d2 = datetime.datetime(int(y), int(match.group('mo2')), int(match.group('d2')), int(match.group('h2')), int(match.group('m2')))
        h = match.group('dd1')
        if h:
            dd = int(h) * 60 + int(match.group('dd2'))
        return cls(
            d1,
            d2,
            dd)
    _re = re.compile(
        r'^((?!#).*CLOCK\:\s+)?'
        r'\s*\[(?P<y1>\d+)\-(?P<mo1>\d+)\-(?P<d1>\d+)[^\]\d]*(?P<h1>\d+)\:(?P<m1>\d+)\]--'
        r'(\[(?P<y2>\d+)\-(?P<mo2>\d+)\-(?P<d2>\d+)[^\]\d]*(?P<h2>\d+)\:(?P<m2>\d+)\]\s+=>\s+(?P<dd1>\d+)\:(?P<dd2>\d+))?'
        )


class OrgDateRepeatedTask(OrgDate):

    """
    Date object to represent repeated tasks.
    """

    _active_default = False

    def __init__(self, start, before, after, active=None):
        super(OrgDateRepeatedTask, self).__init__(start, active=active)
        self._before = before
        self._after = after

    def __repr__(self):
        args = [self._date_to_tuple(self.start), self.before, self.after]
        if self._active is not self._active_default:
            args.append(self._active)
        return '{0}({1})'.format(
            self.__class__.__name__, ', '.join(map(repr, args)))

    def __eq__(self, other):
        return super(OrgDateRepeatedTask, self).__eq__(other) and \
            isinstance(other, self.__class__) and \
            self._before == other._before and \
            self._after == other._after

    @property
    def before(self):
        """
        The state of task before marked as done.

        >>> od = OrgDateRepeatedTask((2005, 9, 1, 16, 10, 0), 'TODO', 'DONE')
        >>> od.before
        'TODO'

        """
        return self._before

    @property
    def after(self):
        """
        The state of task after marked as done.

        >>> od = OrgDateRepeatedTask((2005, 9, 1, 16, 10, 0), 'TODO', 'DONE')
        >>> od.after
        'DONE'

        """
        return self._after
