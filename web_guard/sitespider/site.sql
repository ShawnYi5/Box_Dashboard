create table if not exists site(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	name varchar(50) not null,
	url varchar(200) not null,
	siteStatus int not null,
	path varchar(260) not null
);

create table if not exists webpage(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	link varchar(1024) not null,
	path varchar(260),
	md5  varchar(32),
	lasttime datetime default (datetime('now', 'localtime')),
	resourceType varchar(10) not null,
	depth int,
	reference varchar(1024)
);

create table if not exists pagetitle(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	webpage_id int not null,
	title varchar(260),
	foreign key(webpage_id) references webpage(id)
);

create table if not exists site_webpage(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	site_id int not null,
	webpage_id int not null,
	foreign key(site_id) references site(id),
	foreign key(webpage_id) references webpage(id)
);

/*
create table if not exists jobs(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	name varchar(50) not null,
	site_id_1 int not null,
	site_id_2 int not null,
	jobStatus int not null,
	summary varchar(1024),
	foreign key(site_id_1) references site(id),
	foreign key(site_id_2) references site(id)
);

create table if not exists compRresults(
	id INTEGER PRIMARY KEY AUTOINCREMENT,
	jobs_id int not null,
	webpage_id_1 int,
	webpage_id_2 int,
	summary varchar(1024),
	foreign key(jobs_id) references jobs(id)
);
*/