package kr.co.sample.service.impl;

public class UserDAO {

    public Object selectUserList() {
        return list("sample.user.selectUserList");
    }

    public void insertUser(Object vo) {
        insert("sample.user.insertUser", vo);
    }
}
