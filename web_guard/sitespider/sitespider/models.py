# coding=utf-8
from sqlalchemy import Column, String, Integer, DateTime, create_engine, ForeignKey, or_
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from .settings import SQLLITE_DB_PATH
from datetime import datetime
import time
from sqlalchemy.exc import IntegrityError

SQLLITE_LOCK_TIMEOUT = 60
SQLLITE_LOCK_SLEEP_TIME = 1
Base = declarative_base()

DBSession = None


class CModel_pagetitle(Base):
    __tablename__ = 'pagetitle'
    id = Column(Integer, primary_key=True)
    webpage_id = Column(Integer, ForeignKey('webpage.id'))
    title = Column(String(260))

    def addPagetitle(self, webpage_id, title):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_pagetitle = CModel_pagetitle(webpage_id=webpage_id, title=title)
                session.add(new_pagetitle)
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addPagetitle {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_page_title(self, webpage_id):
        page_title = 'none'
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                webpage = session.query(CModel_pagetitle).filter(CModel_pagetitle.webpage_id == webpage_id).first()
                if webpage:
                    page_title = webpage.title
                return page_title
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_page_title {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def delBySiteid(self, site_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                site_webpages = session.query(CModel_site_webpage).filter(CModel_site_webpage.site_id == site_id).all()
                for site_webpage in site_webpages:
                    session.query(CModel_pagetitle) \
                        .filter(site_webpage.webpage_id == CModel_pagetitle.webpage_id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('delBySiteid {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()


class CModel_webpage(Base):
    __tablename__ = 'webpage'
    id = Column(Integer, primary_key=True)
    link = Column(String(1024))
    path = Column(String(260))
    md5 = Column(String(32))
    lasttime = Column(DateTime(timezone=True), server_default=func.now())
    resourceType = Column(String(10))
    depth = Column(Integer)
    reference = Column(String(1024))

    def delBySiteid(self, site_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                site_webpages = session.query(CModel_site_webpage).filter(CModel_site_webpage.site_id == site_id).all()
                for site_webpage in site_webpages:
                    session.query(CModel_webpage) \
                        .filter(site_webpage.webpage_id == CModel_webpage.id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('delBySiteid {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_path(self, id):
        path = 'none'
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                webpage = session.query(CModel_webpage).filter(CModel_webpage.id == id).first()
                if webpage:
                    path = webpage.path
                return path
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_path {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def addWebPage(self, link, path, md5, resourceType, depth, reference):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_webpage = CModel_webpage(link=link, path=path, md5=md5, resourceType=resourceType, depth=depth,
                                             reference=reference)
                session.add(new_webpage)
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addWebPage {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_webpage_id(self, link, path, md5, resourceType):
        id = 0
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                webpage = session.query(CModel_webpage).filter(CModel_webpage.link == link) \
                    .filter(CModel_webpage.path == path) \
                    .filter(CModel_webpage.md5 == md5) \
                    .filter(CModel_webpage.resourceType == resourceType).first()
                if webpage:
                    id = webpage.id
                return id
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_webpage_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_site_page_count(self, site_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                count = session.query(CModel_site_webpage).filter(CModel_site_webpage.site_id == site_id).count()
                return count
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_site_page_count {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_page_list(self, site_id, limit, offset, filter=None):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                pages = session.query(CModel_webpage, CModel_site_webpage).filter(
                    CModel_site_webpage.site_id == site_id).filter(CModel_site_webpage.webpage_id == CModel_webpage.id)
                if filter and 'md5' in filter:
                    pages = pages.filter(CModel_webpage.md5 == filter['md5'])
                if filter and 'resourceType' in filter:
                    pages = pages.filter(CModel_webpage.resourceType == filter['resourceType'])
                pages = pages.limit(limit).offset(offset).all()
                return pages
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_page_list {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()


class CModel_site_webpage(Base):
    __tablename__ = 'site_webpage'
    id = Column(Integer, primary_key=True)
    site_id = Column(Integer, ForeignKey('site.id'))
    webpage_id = Column(Integer, ForeignKey('webpage.id'))

    def delBySiteid(self, site_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_site_webpage) \
                    .filter(CModel_site_webpage.site_id == site_id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('delBySiteid {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def addSite_Webpage(self, site_id, webpage_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_site_webpage = CModel_site_webpage(site_id=site_id, webpage_id=webpage_id)
                session.add(new_site_webpage)
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addSite_Webpage {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_webpage_link(self, site_id, filter=None):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                links = session.query(CModel_site_webpage, CModel_webpage) \
                    .filter(CModel_site_webpage.webpage_id == CModel_webpage.id) \
                    .filter(CModel_site_webpage.site_id == site_id)
                if filter and 'resourceType' in filter:
                    links = links.filter(CModel_webpage.resourceType == filter['resourceType'])
                return links.all()
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_webpage_link {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_page_id(self, site_id, link):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_site_webpage, CModel_webpage) \
                    .filter(CModel_site_webpage.webpage_id == CModel_webpage.id) \
                    .filter(CModel_site_webpage.site_id == site_id) \
                    .filter(CModel_webpage.link == link) \
                    .first()
                if obj:
                    return obj[1].id
                return 0
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_page_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_id_and_md5(self, site_id, link, resourceType):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_site_webpage, CModel_webpage) \
                    .filter(CModel_site_webpage.webpage_id == CModel_webpage.id) \
                    .filter(CModel_site_webpage.site_id == site_id) \
                    .filter(CModel_webpage.link == link) \
                    .filter(CModel_webpage.resourceType == resourceType) \
                    .first()
                if obj:
                    return (obj[1].id, obj[1].md5, obj[1].path)
                return (0, 'none', 'none')
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_id_and_md5 {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()


class CModel_site(Base):
    __tablename__ = 'site'
    id = Column(Integer, primary_key=True)
    name = Column(String(50))
    url = Column(String(200))
    siteStatus = Column(Integer)
    path = Column(String(260))

    # jobs = relationship("CModel_jobs", order_by="CModel_jobs.id", backref="jobs")
    def delBySiteid(self, site_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_site).filter(CModel_site.id == site_id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('delBySiteid {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_site_id(self, name):
        id = 0
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                site = session.query(CModel_site).filter(CModel_site.name == name).first()
                if site:
                    id = site.id
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_site_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        return id

    def get_site_path(self, id):
        path = None
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                site = session.query(CModel_site).filter(CModel_site.id == id).first()
                if site:
                    path = site.path
                return path
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('updateByName {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def updateByName(self, name, url, siteStatus, path):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_site).filter(CModel_site.name == name).update(
                    {CModel_site.url: url, CModel_site.siteStatus: siteStatus, CModel_site.path: path})
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('updateByName {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def update_siteStatus(self, id, siteStatus):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_site).filter(CModel_site.id == id).update({CModel_site.siteStatus: siteStatus})
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('update_siteStatus {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def addSite(self, name, url, siteStatus, path):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_site = CModel_site(name=name, url=url, siteStatus=siteStatus, path=path)
                session.add(new_site)
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('addSite {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_site_list(self, filter):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                sites = session.query(CModel_site)
                if 'id' in filter:
                    sites = sites.filter(CModel_site.id == filter['id'])
                if 'name' in filter:
                    sites = sites.filter(CModel_site.name == filter['name'])
                if 'url' in filter:
                    sites = sites.filter(CModel_site.url == filter['url'])
                if 'siteStatus' in filter:
                    sites = sites.filter(CModel_site.siteStatus == filter['siteStatus'])
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_site_list {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        return sites.all()


class CModel_ignore_url(Base):
    __tablename__ = 'ignore_url'
    id = Column(Integer, primary_key=True)
    url = Column(String(200), unique=True)

    def del_ignore_url(self, id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_ignore_url).filter(CModel_ignore_url.id == id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('del_ignore_url {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def add_ignore_url(self, url):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_url = CModel_ignore_url(url=url)
                session.add(new_url)
                session.commit()
                return True
            except IntegrityError as e:
                # 违反unique约束
                return False
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('add_ignore_url {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_url_id(self, url):
        url_id = 0
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_ignore_url).filter(CModel_ignore_url.url == url).first()
                if obj:
                    url_id = obj.id
                return url_id
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_url_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()


class CModel_ignore_area(Base):
    __tablename__ = 'ignore_area'
    id = Column(Integer, primary_key=True)
    ignore = Column(String(2000), unique=True)

    def del_area_url(self, id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_ignore_area).filter(CModel_ignore_area.id == id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('del_area_url {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_area_id(self, ignore):
        ignore_id = 0
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_ignore_area).filter(CModel_ignore_area.ignore == ignore).first()
                if obj:
                    ignore_id = obj.id
                return ignore_id
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_area_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def add_ignore_area(self, ignore):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_ignore = CModel_ignore_area(ignore=ignore)
                session.add(new_ignore)
                session.commit()
                return True
            except IntegrityError as e:
                # 违反unique约束
                return False
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('add_ignore_area {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()


class CModel_ignore_url_area(Base):
    __tablename__ = 'ignore_url_area'
    ignore_url_id = Column(Integer, ForeignKey('ignore_url.id'), primary_key=True)
    ignore_area_id = Column(Integer, ForeignKey('ignore_area.id'), primary_key=True)

    def _have_ignore_url_id(self, ignore_url_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_ignore_url_area).filter(
                    CModel_ignore_url_area.ignore_url_id == ignore_url_id).first()
                if obj:
                    return True
                return False
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('_have_ignore_url_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        return True

    def _have_ignore_area_id(self, ignore_area_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_ignore_url_area).filter(
                    CModel_ignore_url_area.ignore_area_id == ignore_area_id).first()
                if obj:
                    return True
                return False
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('_have_ignore_area_id {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        return True

    def del_ignore_url_area(self, ignore_url_id, ignore_area_id):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                session.query(CModel_ignore_url_area).filter(
                    CModel_ignore_url_area.ignore_url_id == ignore_url_id).filter(
                    CModel_ignore_url_area.ignore_area_id == ignore_area_id).delete()
                session.commit()
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('del_ignore_url_area {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        if not self._have_ignore_url_id(ignore_url_id):
            CModel_ignore_url().del_ignore_url(ignore_url_id)
        if not self._have_ignore_area_id(ignore_area_id):
            CModel_ignore_area().del_area_url(ignore_area_id)

    def add_ignore_url_area(self, url, ignore):
        CModel_ignore_url().add_ignore_url(url)
        CModel_ignore_area().add_ignore_area(ignore)

        ignore_url_id = CModel_ignore_url().get_url_id(url)
        ignore_area_id = CModel_ignore_area().get_area_id(ignore)
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                new_ignore = CModel_ignore_url_area(ignore_url_id=ignore_url_id, ignore_area_id=ignore_area_id)
                session.add(new_ignore)
                session.commit()
                return True
            except IntegrityError as e:
                # 违反unique约束
                return False
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('add_ignore_url_area {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_count(self, filter):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                count = session.query(CModel_ignore_url_area).count()
                return count
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_count {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()

    def get_all_list(self, filter, limit, offset):
        st1 = datetime.now()
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_ignore_url, CModel_ignore_area, CModel_ignore_url_area) \
                    .filter(CModel_ignore_url.id == CModel_ignore_url_area.ignore_url_id) \
                    .filter(CModel_ignore_area.id == CModel_ignore_url_area.ignore_area_id)
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_all_list {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        return obj.limit(limit).offset(offset).all()

    def get_re_list(self, url):
        st1 = datetime.now()
        tmpurl = url
        if tmpurl[-1] != '/':
            tmpurl = tmpurl + '/'
        else:
            tmpurl = tmpurl[:-1]
        while True:
            try:
                session = DBSession()
                obj = session.query(CModel_ignore_url, CModel_ignore_area, CModel_ignore_url_area) \
                    .filter(
                    or_(CModel_ignore_url.url == url, CModel_ignore_url.url == tmpurl, CModel_ignore_url.url == '*')) \
                    .filter(CModel_ignore_url.id == CModel_ignore_url_area.ignore_url_id) \
                    .filter(CModel_ignore_area.id == CModel_ignore_url_area.ignore_area_id)
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('get_re_list {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)
            finally:
                session.close()
        return obj.all()


class db_hander(object):
    def __init__(self, db_connect_str):
        st1 = datetime.now()
        while True:
            try:
                self.engine = create_engine(db_connect_str, echo=False)
                self.DBSession = sessionmaker(bind=self.engine)
                self.init_db(self.DBSession())
                break
            except Exception as e:
                st2 = datetime.now()
                if (st2 - st1).seconds > SQLLITE_LOCK_TIMEOUT:
                    raise Exception('db_hander.__init__ {}'.format(e))
                else:
                    time.sleep(SQLLITE_LOCK_SLEEP_TIME)

    def init_db(self, ss):  # 创建所有表
        ss.execute(r'PRAGMA page_size = 4096')
        ss.execute(r'VACUUM')
        ss.execute(r'PRAGMA auto_vacuum = 0')
        ss.execute(r'PRAGMA cache_size = 8192')
        ss.execute(r'PRAGMA synchronous = OFF')
        Base.metadata.create_all(self.engine)

    def drop_db(self):  # 删除所有表
        Base.metadata.drop_all(self.engine)


g_db = db_hander("sqlite:///{}?check_same_thread=False".format(SQLLITE_DB_PATH))
DBSession = g_db.DBSession
