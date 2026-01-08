alter table vcs_connection
    drop constraint vcs_connection_connection_type_check;

alter table vcs_connection
    add constraint vcs_connection_connection_type_check
        check ((connection_type)::text = ANY
               ((ARRAY ['OAUTH_MANUAL'::character varying, 'APP'::character varying, 'CONNECT_APP'::character varying, 'GITHUB_APP'::character varying, 'OAUTH_APP'::character varying, 'PERSONAL_TOKEN'::character varying, 'APPLICATION'::character varying, 'ACCESS_TOKEN'::character varying, 'REPOSITORY_TOKEN'::character varying])::text[]));