#!/usr/bin/env python2
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python fileencoding=utf-8
'''
Copyright © 2018-2019
    Stefan Vigerske <svigerske@gams.com>
This is a modified/extended version of trac-to-gitlab from https://github.com/moimael/trac-to-gitlab.
It has been adapted to fit the needs of a specific Trac to GitLab conversion.
Then it has been adapted to fit the needs to another Trac to GitHub conversion.

Copyright © 2013
    Eric van der Vlist <vdv@dyomedea.com>
    Jens Neuhalfen <http://www.neuhalfen.name/>

This sotfware is free software: you can redistribute it and/or modify
it under the terms of the GNU Lesser General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This sotfware is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Lesser General Public License for more details.

You should have received a copy of the GNU Lesser General Public License
along with this library. If not, see <http://www.gnu.org/licenses/>.
'''

import re
import os
import ConfigParser
import ast
from datetime import datetime
from time import sleep
#from re import MULTILINE
import xmlrpclib
from github import Github, GithubObject, InputFileContent

#import github as gh
#gh.enable_console_debug_logging()

"""
What
=====

 This script migrates issues from trac to github.

License
========

 License: http://www.wtfpl.net/

Requirements
==============

 * Python 2, xmlrpclib, requests
 * Trac with xmlrpc plugin enabled
 * PyGithub

"""

default_config = {
    'ssl_verify': 'no',
    'migrate' : 'true',
    'overwrite' : 'true',
    'exclude_authors' : 'trac',
}

# 6-digit hex notation with leading '#' sign (e.g. #FFAABB) or one of the CSS color names (https://developer.mozilla.org/en-US/docs/Web/CSS/color_value#Color_keywords)
labelcolor = {
  'component' : '08517b',
  'priority' : 'ff0000',
  'severity' : 'ee0000',
  'type' : '008080',
  'keyword' : 'eeeeee'
}

sleep_after_request = 2.0;
sleep_after_10tickets = 600.0;

config = ConfigParser.ConfigParser(default_config)
config.read('migrate.cfg')

trac_url = config.get('source', 'url')
trac_path = None
if config.has_option('source', 'path') :
    trac_path = config.get('source', 'path')

github_api_url = config.get('target', 'url')
github_token = None
if config.has_option('target', 'token') :
    github_token = config.get('target', 'token')
else :
    github_username = config.get('target', 'username')
    github_password = config.get('target', 'password')
github_project = config.get('target', 'project_name')

users_map = ast.literal_eval(config.get('target', 'usernames'))
must_convert_issues = config.getboolean('issues', 'migrate')
only_issues = None
if config.has_option('issues', 'only_issues'):
    only_issues = ast.literal_eval(config.get('issues', 'only_issues'))
blacklist_issues = None
if config.has_option('issues', 'blacklist_issues'):
    blacklist_issues = ast.literal_eval(config.get('issues', 'blacklist_issues'))
filter_issues = 'max=0&order=id'
if config.has_option('issues', 'filter_issues') :
    filter_issues = config.get('issues', 'filter_issues')
migrate_keywords = config.getboolean('issues', 'migrate_keywords')
migrate_milestones = config.getboolean('issues', 'migrate_milestones')
add_label = None
if config.has_option('issues', 'add_label'):
    add_label = config.get('issues', 'add_label')

svngit_mapfile = None
if config.has_option('source', 'svngitmap') :
    svngit_mapfile = config.get('source', 'svngitmap')
svngit_map = None

#pattern_changeset = r'(?sm)In \[changeset:"([^"/]+?)(?:/[^"]+)?"\]:\n\{\{\{(\n#![^\n]+)?\n(.*?)\n\}\}\}'
pattern_changeset = r'(?sm)In \[changeset:"[0-9]+" ([0-9]+)\]:\n\{\{\{(\n#![^\n]+)?\n(.*?)\n\}\}\}'
matcher_changeset = re.compile(pattern_changeset)

pattern_changeset2 = r'\[changeset:([a-zA-Z0-9]+)\]'
matcher_changeset2 = re.compile(pattern_changeset2)

pattern_svnrev1 = r'(?:\bchangeset *)?\[([0-9]+)\]'
matcher_svnrev1 = re.compile(pattern_svnrev1)

pattern_svnrev2 = r'\b(?:changeset *)?r([0-9]+)\b'
matcher_svnrev2 = re.compile(pattern_svnrev2)

gh_labels = dict()
gh_user = None

def format_changeset_comment(m):
    if svngit_map is not None and m.group(1) in svngit_map :
        r = 'In ' + svngit_map[m.group(1)][0][:10]
    else :
        if svngit_map is not None :
            print '  WARNING: svn revision', m.group(1), 'not given in svn to git mapping'
        r = 'In changeset ' + m.group(1)
    r += ':\n> ' + m.group(3).replace('\n', '\n> ')
    return r


def handle_svnrev_reference(m) :
    assert svngit_map is not None
    if m.group(1) in svngit_map :
        return svngit_map[m.group(1)][0][:10]
    else :
        #print '  WARNING: svn revision', m.group(1), 'not given in svn to git mapping'
        return m.group(0)


def trac2markdown(text, base_path, multilines = True) :
    text = matcher_changeset.sub(format_changeset_comment, text)
    text = matcher_changeset2.sub(r'\1', text)

    text = re.sub('\r\n', '\n', text)
    text = re.sub(r'{{{(.*?)}}}', r'`\1`', text)
    text = re.sub(r'(?sm){{{(\n?#![^\n]+)?\n(.*?)\n}}}', r'```\n\2\n```', text)

    text = text.replace('[[TOC]]', '')
    text = text.replace('[[BR]]', '\n')
    text = text.replace('[[br]]', '\n')
    text = text.replace("@", "`@`")

    if svngit_map is not None :
        text = matcher_svnrev1.sub(handle_svnrev_reference, text)
        text = matcher_svnrev2.sub(handle_svnrev_reference, text)

    if multilines:
        text = re.sub(r'^\S[^\n]+([^=-_|])\n([^\s`*0-9#=->-_|])', r'\1 \2', text)

    text = re.sub(r'(?m)^======\s+(.*?)\s+======$', r'\n###### \1', text)
    text = re.sub(r'(?m)^=====\s+(.*?)\s+=====$', r'\n##### \1', text)
    text = re.sub(r'(?m)^====\s+(.*?)\s+====$', r'\n#### \1', text)
    text = re.sub(r'(?m)^===\s+(.*?)\s+===$', r'\n### \1', text)
    text = re.sub(r'(?m)^==\s+(.*?)\s+==$', r'\n## \1', text)
    text = re.sub(r'(?m)^=\s+(.*?)\s+=$', r'\n# \1', text)
    text = re.sub(r'^             * ', r'****', text)
    text = re.sub(r'^         * ', r'***', text)
    text = re.sub(r'^     * ', r'**', text)
    text = re.sub(r'^ * ', r'*', text)
    text = re.sub(r'^ \d+. ', r'1.', text)

    a = []
    is_table = False
    for line in text.split('\n'):
        if not line.startswith('    '):
            line = re.sub(r'\[\[(https?://[^\s\[\]\|]+)\s*[\s\|]\s*([^\[\]]+)\]\]', r'[\2](\1)', line)
            line = re.sub(r'\[(https?://[^\s\[\]\|]+)\s*[\s\|]\s*([^\[\]]+)\]', r'[\2](\1)', line)
            line = re.sub(r'\[wiki:([^\s\[\]]+)\s([^\[\]]+)\]', r'[\2](%s/\1)' % os.path.relpath('/wikis/', base_path), line)
            line = re.sub(r'\[/wiki/([^\s\[\]]+)\s([^\[\]]+)\]', r'[\2](%s/\1)' % os.path.relpath('/wikis/', base_path), line)
            line = re.sub(r'\[source:([^\s\[\]]+)\s([^\[\]]+)\]', r'[\2](%s/\1)' % os.path.relpath('/tree/master/', base_path), line)
            line = re.sub(r'source:([\S]+)', r'[\1](%s/\1)' % os.path.relpath('/tree/master/', base_path), line)
            line = re.sub(r'\!(([A-Z][a-z0-9]+){2,})', r'\1', line)
            line = re.sub(r'\[\[Image\(source:([^(]+)\)\]\]', r'![](%s/\1)' % os.path.relpath('/tree/master/', base_path), line)
            line = re.sub(r'\[\[Image\(([^(]+)\)\]\]', r'![](\1)', line)
            line = re.sub(r'\'\'\'(.*?)\'\'\'', r'*\1*', line)
            line = re.sub(r'\'\'(.*?)\'\'', r'_\1_', line)
            if line.startswith('||'):
                if not is_table:
                    sep = re.sub(r'[^|]', r'-', line)
                    line = line + '\n' + sep
                    is_table = True
                line = re.sub(r'\|\|', r'|', line)
            else:
                is_table = False
        else:
            is_table = False
        a.append(line)
    text = '\n'.join(a)
    return text


def convert_xmlrpc_datetime(dt):
    # datetime.strptime(str(dt), "%Y%m%dT%X").isoformat() + "Z"
    return datetime.strptime(str(dt), "%Y%m%dT%H:%M:%S")

def gh_create_milestone(dest, milestone_data) :
    if dest is None : return None

    milestone = dest.create_milestone(milestone_data['title'], milestone_data['state'], milestone_data['description'], milestone_data.get('due_date', GitlabObject.NotSet) )
    sleep(sleep_after_request)
    return milestone

def gh_ensure_label(dest, labelname, labelcolor) :
    if dest is None : return
    if labelname.lower() in gh_labels :
        return
    print 'Create label %s with color #%s' % (labelname, labelcolor);
    gh_label = dest.create_label(labelname, labelcolor);
    gh_labels[labelname.lower()] = gh_label;
    sleep(sleep_after_request)

def gh_create_issue(dest, issue_data) :
    if dest is None : return None

    if 'labels' in issue_data :
        labels = [gh_labels[label.lower()] for label in issue_data['labels']]
    else :
        labels = GithubObject.NotSet

    gh_issue = dest.create_issue(issue_data['title'],
                                 issue_data['description'],
                                 assignee = issue_data.get('assignee', GithubObject.NotSet),
                                 milestone = issue_data.get('milestone', GithubObject.NotSet),
                                 labels = labels)
    print("  created issue " + str(gh_issue))
    sleep(sleep_after_request)

    return gh_issue

def gh_comment_issue(dest, issue, comment) :
    if dest is None : return

    # upload attachement, if there is one
    if 'attachment_name' in comment :
        assert gh_user is not None
        filename = comment['attachment_name']
        gistname = dest.name + ' issue ' + str(issue.number) + ' attachment ' + filename
        filecontent = InputFileContent(comment['attachment'])
        try :
            gist = gh_user.create_gist(False,
                                       { gistname : filecontent },
                                       'Attachment %s to Ipopt issue #%d created by %s at %s' % (filename, issue.number, comment['author'], comment['created_at']) )
            note = 'Attachment [%s](%s) by %s created at %s' % (filename, gist.files[gistname].raw_url, comment['author'], comment['created_at'])
        except UnicodeDecodeError :
            note = 'Binary attachment %s by %s created at %s lost by Trac to GitHub conversion.' % (filename, comment['author'], comment['created_at'])
            print '  LOOSING ATTACHMENT', filename, 'in issue', issue.number
        sleep(sleep_after_request)
        if 'note' in comment and comment['note'] != '' :
            note += '\n\n' + comment['note']
    else :
        note = 'Comment by %s created at %s' % (comment['author'], comment['created_at'])
        if 'note' in comment and comment['note'] != '' :
            note += '\n\n' + comment['note']

    issue.create_comment(note)
    sleep(sleep_after_request)

def gh_update_issue_property(dest, issue, key, val) :
    if dest is None : return

    if key == 'labels' :
        labels = [gh_labels[label.lower()] for label in val]
        issue.set_labels(*labels)
    elif key == 'assignee' :
        if issue.assignee == val :
            return
        if len(issue.assignees) > 0 :
            issue.remove__from_assignees(issue.assignee)
        if val is not None and val is not GithubObject.NotSet and val != '' :
            issue.add_to_assignees(val)
    elif key == 'state' :
        issue.edit(state = val)
    elif key == 'description' :
        issue.edit(body = val)
    elif key == 'title' :
        issue.edit(title = val)
    elif key == 'milestone' :
        issue.edit(milestone = val)
    else :
        raise 'Unknown key ' + key

    sleep(sleep_after_request)

def gh_username(dest, origname) :
    if origname in users_map :
        return '@' + users_map[origname]
    assert not origname.startswith('@')
    return origname;

def convert_issues(source, dest, only_issues = None, blacklist_issues = None):
    milestone_map = {}

    if migrate_milestones:
        for milestone_name in source.ticket.milestone.getAll():
            milestone = source.ticket.milestone.get(milestone_name)
            print("Creating milestone " + milestone['name'])
            new_milestone = {
                'description' : trac2markdown(milestone['description'], '/milestones/', False),
                'title' : milestone['name'],
                'state' : 'open' if str(milestone['completed']) == '0'  else 'closed'
            }
            if milestone['due']:
                new_milestone['due_date'] = milestone['due']  #convert_xmlrpc_datetime(milestone['due'])
            milestone_map[milestone_name] = gh_create_milestone(dest, new_milestone)

    get_all_tickets = xmlrpclib.MultiCall(source)

    for ticket in source.ticket.query(filter_issues):
        get_all_tickets.ticket.get(ticket)

    nextticketid = 1;
    ticketcount = 0;
    for src_ticket in get_all_tickets():
        #src_ticket is [id, time_created, time_changed, attributes]
        src_ticket_id = src_ticket[0]
        if only_issues and src_ticket_id not in only_issues:
            print("SKIP unwanted ticket #%s" % src_ticket_id)
            continue
        if blacklist_issues and src_ticket_id in blacklist_issues:
            print("SKIP blacklisted ticket #%s" % src_ticket_id)
            continue

        if not only_issues and not blacklist_issues and not config.has_option('issues', 'filter_issues') :
            while nextticketid < src_ticket_id :
                print("Ticket %d missing in Trac. Generating empty one in GitHub." % nextticketid)

                issue_data = {
                    'title' : 'Deleted trac ticket %d' % nextticketid,
                    'description' : 'Ticket %d had been deleted in the original Trac instance. This empty ticket serves as placeholder to ensure a proper 1:1 mapping of ticket ids to issue ids.',
                    'labels' : []
                }

                issue = gh_create_issue(dest, issue_data)
                gh_update_issue_property(dest, issue, 'state', 'closed')

                nextticketid = nextticketid+1

        nextticketid = nextticketid+1;

        src_ticket_data = src_ticket[3]
        # src_ticket_data.keys(): ['status', 'changetime', 'description', 'reporter', 'cc', 'type', 'milestone', '_ts',
        # 'component', 'owner', 'summary', 'platform', 'version', 'time', 'keywords', 'resolution']

        changelog = source.ticket.changeLog(src_ticket_id)

        print(("Migrate ticket #%s (%d changes): %s" % (src_ticket_id, len(changelog), src_ticket_data['summary'][:30])).encode("ascii", "replace"));

        # get original component, owner
        # src_ticket_data['component'] is the component after all changes, but for creating the issue we want the component
        # that was set when the issue was created; we should get this from the first changelog entry that changed a component
        # ... and similar for other attributes
        component = None
        owner = None
        version = None
        tickettype = None
        description = None
        summary = None
        priority = None
        severity = None
        keywords = None
        status = None
        for change in changelog :
            #change is tuple (time, author, field, oldvalue, newvalue, permanent)
            if component is None and change[2] == 'component' :
                component = change[3]
                continue
            if owner is None and change[2] == 'owner' :
                owner = change[3]
                continue
            if version is None and change[2] == 'version' :
                version = change[3]
                continue
            if tickettype is None and change[2] == 'type' :
                tickettype = change[3]
                continue
            if description is None and change[2] == 'description' :
                description = change[3]
                continue
            if summary is None and change[2] == 'summary' :
                summary = change[3]
                continue
            if priority is None and change[2] == 'priority' :
                priority = change[3]
                continue
            if severity is None and change[2] == 'severity' :
                severity = change[3]
                continue
            if keywords is None and change[2] == 'keywords' :
                keywords = change[3]
                continue
            if status is None and change[2] == 'status' :
                status = change[3]
                continue

        # if no change changed a certain attribute, then that attribute is given by ticket data
        if component is None :
            component = src_ticket_data['component']
        if owner is None :
            owner = src_ticket_data['owner']
        if version is None :
            version = src_ticket_data.get('version')
        if tickettype is None :
            tickettype = src_ticket_data['type']
        if description is None :
            description = src_ticket_data['description']
        if summary is None :
            summary = src_ticket_data['summary']
        if priority is None :
            priority = src_ticket_data.get('priority', 'normal')
        if severity is None :
            severity = src_ticket_data.get('severity', 'normal')
        if keywords is None :
            keywords = src_ticket_data['keywords']
        if status is None :
            status = src_ticket_data['status']
        reporter = gh_username(dest, src_ticket_data['reporter']);

        labels = []
        if add_label:
            labels.append(add_label)
        labels.append(component)
        gh_ensure_label(dest, component, labelcolor['component'])
        if priority != 'normal' :
            labels.append(priority)
            gh_ensure_label(dest, priority, labelcolor['priority'])
        if severity != 'normal' :
            labels.append(severity)
            gh_ensure_label(dest, severity, labelcolor['severity'])
        labels.append(tickettype)
        gh_ensure_label(dest, tickettype, labelcolor['type'])
        if keywords != '' and migrate_keywords:
            for keyword in keywords.split(','):
                labels.append(keyword.strip())
                gh_ensure_label(dest, keyword.strip(), labelcolor['keyword'])

        description_pre = 'Issue created by migration from Trac.\n\n'
        description_pre += 'Original creator: ' + reporter + '\n\n'
        description_pre += 'Original creation time: ' + str(convert_xmlrpc_datetime(src_ticket[1])) + '\n\n'

        assignee = GithubObject.NotSet
        if owner != '' :
            assignee = gh_username(dest, owner)
            if not assignee.startswith('@'):
                description_pre += 'Assignee: ' + assignee + '\n\n'
                assignee = GithubObject.NotSet

        if version is not None and version != 'trunk' :
            description_pre += 'Version: ' + version + '\n\n'

        # subscribe persons in cc
        cc = src_ticket_data.get('cc', '').lower()
        ccstr = ''
        for person in cc.split(',') :
            person = person.strip()
            if person == '' : continue
            person = gh_username(dest, person)
            ccstr += ' ' + person
        if ccstr != '' :
            description_pre += 'CC: ' + ccstr + '\n\n'

        description = description_pre + trac2markdown(description, '/issues/', False)
        assert description.find('/wikis/') < 0

        # collect all parameters
        issue_data = {
            'title' : summary,
            'description' : description,
            'labels' : labels,
            'assignee' : assignee
        }

        if 'milestone' in src_ticket_data:
            milestone = src_ticket_data['milestone']
            if milestone  and milestone in milestone_map:
                issue_data['milestone'] = milestone_map[milestone]

        issue = gh_create_issue(dest, issue_data)

        # handle status
        if status in ['new', 'assigned', 'analyzed', 'reopened'] :
            issue_state = 'open'
        elif status in ['closed'] :
            # sometimes a ticket is already closed at creation, so close issue
            issue_state = 'closed'
            gh_update_issue_property(dest, issue, 'state', 'closed')
        else :
            raise("  unknown ticket status: " + status)

        attachment = None
        for change in changelog:
            #change is tuple (time, author, field, oldvalue, newvalue, permanent)
            change_time = str(convert_xmlrpc_datetime(change[0]))
            change_type = change[2]
            print(("  %s by %s (%s -> %s)" % (change_type, change[1], change[3][:40].replace("\n", " "), change[4][:40].replace("\n", " "))).encode("ascii", "replace"))
            assert attachment is None or change_type == "comment", "an attachment must be followed by a comment"
            if change[1] in ['anonymous', 'Draftmen888'] :
                print "  SKIPPING CHANGE BY", change[1]
                continue
            author = gh_username(dest, change[1])
            if change_type == "attachment":
                # The attachment will be described in the next change!
                attachment = change
            elif change_type == "comment":
                # change[3] is here either x or y.x, where x is the number of this comment and y is the number of the comment that is replied to
                desc = change[4].strip();
                if desc == '' and attachment is None :
                    # empty description and not description of attachment
                    continue
                note = {
                    'note' : trac2markdown(desc, '/issues/', False)
                }
                if attachment is not None :
                    note['attachment_name'] = attachment[4]  # name of attachment
                    note['attachment'] = source.ticket.getAttachment(src_ticket_id, attachment[4].encode('utf8')).data
                    attachment = None
                note['created_at'] = change_time
                note['author'] = author
                gh_comment_issue(dest, issue, note)
            elif change_type.startswith("_comment") :
                # this is an old version of a comment, which has been edited later (given in previous change),
                # e.g., see http://localhost:8080/ticket/3431#comment:9 http://localhost:8080/ticket/3400#comment:14
                # we will forget about these old versions and only keep the latest one
                pass
            elif change_type == "status" :
                # we map here the various statii we have in trac to just 2 statii in gitlab (open or close), so loose some information
                if change[4] in ['new', 'assigned', 'analyzed', 'reopened'] :
                    newstate = 'open'
                    # should not need an extra comment if closing ticket
                    gh_comment_issue(dest, issue, {'note' : 'Changing status from ' + change[3] + ' to ' + change[4] + '.', 'created_at' : change_time, 'author' : author})
                elif change[4] in ['closed'] :
                    newstate = 'closed'
                else :
                    raise("  unknown ticket status: " + change[4])

                if issue_state != newstate :
                    issue_state = newstate
                    gh_update_issue_property(dest, issue, 'state', newstate)

            elif change_type == "resolution" :
                if change[3] != '' :
                    desc = "Resolution changed from %s to %s" % (change[3], change[4])
                else :
                    desc = "Resolution: " + change[4]
                note = {
                    'note' : desc,
                    'author' : author,
                    'created_at' : change_time
                }
                gh_comment_issue(dest, issue, note)
            elif change_type == "component" :
                labels.remove(change[3])
                labels.append(change[4])
                gh_ensure_label(dest, change[4], labelcolor['component'])
                gh_comment_issue(dest, issue, { 'note' : 'Changing component from ' + change[3] + ' to ' + change[4] + '.', 'created_at' : change_time, 'author' : author })
                gh_update_issue_property(dest, issue, 'labels', labels)
            elif change_type == "owner" :
                if change[3] != '' and change[4] != '' :
                    gh_comment_issue(dest, issue, { 'note' : 'Changing assignee from ' + gh_username(dest, change[3]) + ' to ' + gh_username(dest, change[4]) + '.', 'created_at' : change_time, 'author' : author })
                elif change[3] == '' :
                    gh_comment_issue(dest, issue, { 'note' : 'Set assignee to ' + gh_username(dest, change[4]) + '.', 'created_at' : change_time, 'author' : author })
                else :
                    gh_comment_issue(dest, issue, { 'note' : 'Remove assignee ' + gh_username(dest, change[3]) + '.', 'created_at' : change_time, 'author' : author })

                if change[4] != change[3] :
                    assignee = gh_username(dest, change[4])
                    if not assignee.startswith('@') :
                        assignee = GithubObject.NotSet
                    gh_update_issue_property(dest, issue, 'assignee', assignee)
            elif change_type == "version" :
                if change[3] != '' :
                    desc = "Version changed from %s to %s" % (change[3], change[4])
                else :
                    desc = "Version: " + change[4]
                note = {
                    'note' : "Version: " + desc,
                    'author' : author,
                    'created_at' : change_time
                }
                gh_comment_issue(dest, issue, note)
            elif change_type == "milestone" :
                if change[4] != '' and change[4] in milestone_map:
                    issue_data['milestone'] = milestone_map[change[4]]
                elif 'milestone' in issue_data :
                    del issue_data['milestone']
                gh_update_issue_property(dest, issue, 'milestone', issue_data.get('milestone', GithubObject.NotSet))
            elif change_type == "cc" :
                pass  # we handle only the final list of CCs (above)
            elif change_type == "type" :
                labels.remove(change[3])
                labels.append(change[4])
                gh_ensure_label(dest, change[4], labelcolor['type'])
                gh_comment_issue(dest, issue, { 'note' : 'Changing type from ' + change[3] + ' to ' + change[4] + '.', 'created_at' : change_time, 'author' : author })
                gh_update_issue_property(dest, issue, 'labels', labels)
            elif change_type == "description" :
                issue_data['description'] = description_pre + trac2markdown(change[4], '/issues/', False) + '\n\n(changed by ' + author + ' at ' + change_time + ')'
                gh_update_issue_property(dest, issue, 'description', issue_data['description'])
            elif change_type == "summary" :
                issue_data['title'] = change[4]
                gh_update_issue_property(dest, issue, 'title', issue_data['title'])
            elif change_type == "priority" :
                if change[3] != '' and change[3] != 'normal' :
                    labels.remove(change[3])
                if change[4] != '' and change[4] != 'normal' :
                    labels.append(change[4])
                    gh_ensure_label(dest, change[4], labelcolor['priority'])
                    gh_comment_issue(dest, issue, { 'note' : 'Changing priority from ' + change[3] + ' to ' + change[4] + '.', 'created_at' : change_time, 'author' : author })
                gh_update_issue_property(dest, issue, 'labels', labels)
            elif change_type == "severity" :
                if change[3] != '' and change[3] != 'normal' :
                    labels.remove(change[3])
                if change[4] != '' and change[4] != 'normal' :
                    labels.append(change[4])
                    gh_ensure_label(dest, change[4], labelcolor['severity'])
                    gh_comment_issue(dest, issue, { 'note' : 'Changing severity from ' + change[3] + ' to ' + change[4] + '.', 'created_at' : change_time, 'author' : author })
                gh_update_issue_property(dest, issue, 'labels', labels)
            elif change_type == "keywords" :
                if not migrate_keywords : continue
                oldkeywords = change[3].split(',')
                newkeywords = change[4].split(',')
                for keyword in oldkeywords :
                    keyword = keyword.strip()
                    if keyword != '' :
                        labels.remove(keyword)
                for keyword in newkeywords :
                    keyword = keyword.strip()
                    if keyword != '' :
                        labels.append(keyword)
                        gh_ensure_label(dest, keyword, labelcolor['keyword'])
                oldkeywords = [ kw.strip() for kw in oldkeywords ]
                newkeywords = [ kw.strip() for kw in newkeywords ]
                gh_comment_issue(dest, issue, { 'note' : 'Changing keywords from ' + ','.join(oldkeywords) + ' to ' + ','.join(newkeywords) + '".', 'created_at' : change_time, 'author' : author })
                gh_update_issue_property(dest, issue, 'labels', labels)
            else :
                raise BaseException("Unknown change type " + change_type)
        assert attachment is None

        ticketcount = ticketcount + 1
        if ticketcount % 10 == 0 :
            print '%d tickets migrated. Waiting %d seconds to let GitHub cool down.' % (ticketcount, sleep_after_10tickets)
            sleep(sleep_after_10tickets)

if __name__ == "__main__":
    source = xmlrpclib.ServerProxy(trac_url)

    dest = None
    if github_token is not None :
        github = Github(github_token, base_url=github_api_url)
    else :
        github = Github(github_username, github_password, base_url=github_api_url)
    dest = github.get_repo(github_project)
    gh_user = github.get_user()

    if dest is not None :
        for l in dest.get_labels() :
            gh_labels[l.name.lower()] = l
        #print 'Existing labels:', gh_labels.keys()

    if svngit_mapfile is not None :
        svngit_map = dict()
        for line in open(svngit_mapfile, 'r') :
            l = line.split()
            assert len(l) >= 2, line
            githash = l[0]
            svnrev = l[1]
            svnbranch = l[2][1:] if len(l) > 2 else 'trunk'
            #print l[1], l[0]
            # if already have a svn revision entry from branch trunk, then ignore others
            if svnrev in svngit_map and svngit_map[svnrev][1] == 'trunk' :
                continue
            svngit_map[svnrev] = [githash, svnbranch]

    if must_convert_issues:
        convert_issues(source, dest, only_issues = only_issues, blacklist_issues = blacklist_issues)
