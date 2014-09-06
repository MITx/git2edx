#!/usr/bin/python
#
# File:   git2edx.py
# Date:   12-Mar-14
# Author: I. Chuang <ichuang@mit.edu>
#
# web server which provides git2edx service
# uses wsgi
#
# configuration parameters in config.py

import sys, string, re, time, os
import cgi
import json
import urllib
import requests
import socket

from cgi import parse_qs, escape
from lxml import etree
from edxStudio import edxStudio

#-----------------------------------------------------------------------------
# config

config = {'username': "",
          'password': "",
          'ANYREPO': True,
          'FORCE_BRANCH': '',
          'REPODIR': '',
          'LOGFILE': "",
          'REPO2COURSE_MAP': {},	# if empty, then course id will be determined from repo XML contents
          'PORT': 8121,
          }

CFN = 'config.json'
if os.path.exists(CFN):
    import json
    new_config = json.loads(open(CFN).read())
    config.update(new_config)

#-----------------------------------------------------------------------------

PIDFILE = "git2edx.pid"

open(PIDFILE,'w').write(str(os.getpid()))

def LOG(x):
    fp = open(config['LOGFILE'],'a')
    if type(x)==dict:
        for k in x:
            if not k:
                continue
            s = '  %s : %s' % (k,x[k])
            fp.write(s)
            print s
    #if type(x)==type('str'):
    else:
        fp.write(x)
        fp.write('\n')
        print x

    sys.stdout.flush()
    fp.flush()
    fp.close()

#-----------------------------------------------------------------------------

def upload_to_edx(rdir, repo, r2c=None):
    '''
    tar up repo, and upload to edX

    rdir = directory with repo contents
    repo = repo name
    r2c = repo-to-course config dict

    Specifying an r2c is optional; if unspecified, then an r2c entry will be searched for,
    in the config file.

    An r2c entry may be provided in cases when one repo is chained to trigger loading of
    one or more courses.
    '''
    
    site_url = "https://studio.edx.org"
    
    if r2c is None:
        
        # get a r2c entry from scratch or just course_id from repo course.xml file
        r2c = {}
    
        # get course_id (and optional site_url)
        if not config['REPO2COURSE_MAP']:
            cxml = etree.parse(open('%s/course.xml' % rdir)).getroot()
            org = cxml.get('org')
            course = cxml.get('course')
            sem = cxml.get('url_name')
            course_id = '/'.join([org, course, sem])
        else:
            r2c = config['REPO2COURSE_MAP'].get(repo, '')
            if isinstance(r2c, dict):
                course_id = r2c['cid']
                site_url = r2c['site']
            else:
                course_id = r2c

    else:	# r2c already specified (it should be a dict)
        course_id = r2c['cid']
        site_url = r2c['site']
        
    if not course_id:
        LOG("Error: cannot determine course_id for repo=%s" % repo)
        return
    
    # if branch specified check that out now
    if 'branch' in r2c:
        os.chdir(rdir)
        cmd = 'git checkout %s; git pull' % r2c['branch']
        LOG(cmd)
        LOG(os.popen(cmd).read())

    # if course.xml is overriden, do that now
    oldxml = ''
    if 'coursexml' in r2c:
        os.chdir(rdir)
        os.chdir('..')
        oldxml = '%s/course.xml' % repo
        tmpxml = '%s/course.xml.orig' % repo
        newxml = '%s/%s' % (repo, r2c['coursexml'])
        os.rename(oldxml, tmpxml)
        LOG(os.popen("cp '%s' '%s'" % (newxml, oldxml)).read())
        LOG("  Moving %s -> %s; %s -> %s" % (oldxml, tmpxml, newxml, oldxml))

    # create tar.gz file
    tfn = '%s.tar.gz' % rdir
    os.chdir(rdir)
    os.chdir('..')
    cmd = "tar czf %s --exclude=.git --exclude=src %s" % (tfn, repo)
    LOG(cmd)
    LOG(os.popen(cmd).read())
    
    # undo change of course.xml if that was done
    if 'coursexml' in r2c:
        os.rename(tmpxml, oldxml)
        LOG("  Moving %s -> %s" % (tmpxml, oldxml))

    # upload to studio
    LOG('-'*30 + "Uploading %s to edX studio course_id=%s" % (tfn, course_id))
    es = edxStudio(username=config['username'], password=config['password'], base=site_url)
    es.do_upload(course_id, tfn, nwait=3)

    # if there is a "chainto" entry, then call this procedure with the next chain link
    chainto = r2c.get('chainto', '')
    if chainto:
        LOG('--> chainto triggering auto-load of %s' % chainto)
        upload_to_edx(rdir, repo, config['REPO2COURSE_MAP'].get(chainto, None))

#-----------------------------------------------------------------------------

def do_git2edx(environ, start_response):

    if True:

        # the environment variable CONTENT_LENGTH may be empty or missing
        try:
            request_body_size = int(environ.get('CONTENT_LENGTH', 0))
        except (ValueError):
            request_body_size = 0
                   
        # When the method is POST the query string will be sent
        # in the HTTP request body which is passed by the WSGI server
        # in the file like wsgi.input environment variable.
        request_body = environ['wsgi.input'].read(request_body_size)
        post = parse_qs(request_body)

	LOG('-----------------------------------------------------------------------------')
	LOG('connect at %s' % time.ctime(time.time()))

	args = post

        LOG('environ = %s' % environ)
        LOG('post = %s' % post)
        LOG('payload = "%s"' % args.get('payload','none'))

	# from github
	if 'payload' in args:
	    gitargs = json.loads(args['payload'][0])
	    repo = gitargs['repository']['name']
	    LOG('repo = %s\n' % repo)

            REPOS = os.listdir(config['REPODIR'])

            if config['ANYREPO'] and repo not in REPOS:
                os.chdir(config['REPODIR'])
                # should do a git clone here...

	    # if it is one of our repos, then try doing a git2edx on it
	    if config['ANYREPO'] or repo in REPOS:
		rdir = '%s/%s' % (config['REPODIR'],repo)
		os.chdir(rdir)
                # figure out which branch the directory is on
                branch = ''
                for k in os.popen('git branch').readlines():
                    if k[0]=='*':
                        branch = k[2:].strip()
                LOG('Current branch = "%s"' % branch)

                # get current branch
                #cmd = "git reset --hard HEAD; git clean -f -d; git pull origin"
                #LOG(cmd)
		#LOG(os.popen(cmd).read())

                # get branch
                cmd = "git reset --hard HEAD; git clean -f -d; git pull origin %s" % branch
                LOG(cmd)
		LOG(os.popen(cmd).read())

                if config['FORCE_BRANCH']:
                    cmd = 'git checkout %s' % config['FORCE_BRANCH']
                    LOG(cmd)
                    LOG(os.popen(cmd).read())
                    
		# tar it up and upload to edX studio site
                upload_to_edx(rdir, repo)

	    else:
		LOG("unknown\n")

        start_response('200 OK', [('Content-Type', 'text/html')])
        return ['''Hello World - github''']
        LOG("done at %s" % time.ctime(time.time()))

if __name__ == '__main__':
    LOG('========> started at %s' % time.ctime(time.time()))
    from wsgiref.simple_server import make_server
    import socket
    host = socket.gethostname()
    srv = make_server(host, PORT, do_git2edx)
    srv.serve_forever()
    
